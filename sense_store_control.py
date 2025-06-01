import asyncio

import smbus2
import bme280
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
import time

from kasa import Discover
from collections import deque
import statistics

import requests
import re

port = 1
address = 0x76
bus = smbus2.SMBus(port)

target_humidity_normal = 51

async def main():
	up_counter = 0
	down_counter = 0
	dehumid_flag = await get_dehumid_status()
	humidity_history = deque(maxlen=20)  # Store last 10 minutes
	avg_humidity = 100

	while True:
		bme280_data = retrieve_and_store(dehumid_flag) #retrieve and store sensor data for Grafana
		humidity_history.append(bme280_data.humidity)
		
		# Check if we have enough history to calculate average
		if len(humidity_history) == humidity_history.maxlen:
			avg_humidity = statistics.mean(humidity_history)
		
		#run algo to decide if dehumidifier should be on
		target_humidity = target_humidity_normal
		upper_bound = target_humidity + 3.5 # default mode
		lower_bound = target_humidity - 2
		battery_life = get_ecoflow_soc()
		print(f"Battery life: {battery_life}%")
		if battery_life > 85: # aggresive mode cause battery is full
			upper_bound = target_humidity + 2
			lower_bound = target_humidity - 3.5
			target_humidity = target_humidity - 1
		if battery_life > 95: # really aggresive mode cause battery is full
			target_humidity = target_humidity - 3
			upper_bound = target_humidity + 2
			lower_bound = target_humidity - 3.5
			

		if bme280_data.humidity > upper_bound:
			up_counter = up_counter + 1

		if bme280_data.humidity < lower_bound:
			down_counter = down_counter + 1

		if up_counter > 10 and dehumid_flag == False:
			await dehumid_on()
			up_counter = 0
			down_counter = 0
			print("Counter reset after dehumidifier on")
			dehumid_flag = await get_dehumid_status()
		elif down_counter > 10 and dehumid_flag == True:
			await dehumid_off()
			up_counter = 0
			down_counter = 0
			print("Counter reset after dehumidifier off")
			dehumid_flag = await get_dehumid_status()
		elif bme280_data.humidity >= avg_humidity and dehumid_flag == True and bme280_data.humidity < target_humidity and (len(humidity_history) == humidity_history.maxlen):
			await dehumid_off()
			up_counter = 0
			down_counter = 0
			print("Counter reset after dehumidifier off due to no humidity decrease")
			dehumid_flag = await get_dehumid_status()

		time.sleep(30)
	return
	
def retrieve_and_store(dehumid_flag):
	calibration_params = bme280.load_calibration_params(bus, address)
	data = bme280.sample(bus, address, calibration_params)

	# Prometheus setup
	registry = CollectorRegistry()
	g_temp = Gauge('sensor_temperature_celsius', 'Temperature in Celsius', registry=registry)
	g_press = Gauge('sensor_pressure_hpa', 'Pressure in hPa', registry=registry)
	g_hum = Gauge('sensor_humidity_percent', 'Humidity in %', registry=registry)
	humidifer_status = Gauge('dehumid_flag', 'dehumidifer status flag', registry=registry)


	g_temp.set(data.temperature)
	g_press.set(data.pressure)
	g_hum.set(data.humidity)
	humidifer_status.set(int(dehumid_flag))

	push_to_gateway('localhost:9091', job='bme280_sensor', registry=registry)

	# Optional: print values
	print(f"Timestamp: {data.timestamp}, Humidity: {data.humidity} %H, Dehumidifier Status: {dehumid_flag}")

	return data

async def get_dehumid_status():
	print("checking dehumidifier status")
	dev = await Discover.discover_single("192.168.1.106")
	await dev.update()
	print("Dehumidier is on: " + str(dev.is_on))
	return dev.is_on

async def dehumid_on():
	print("sending dehumidifer on signal")
	dev = await Discover.discover_single("192.168.1.106")
	await dev.turn_on()
	await dev.update()
	await get_dehumid_status()
	return

async def dehumid_off():
	print("sending dehumidifer off signal")
	dev = await Discover.discover_single("192.168.1.106")
	await dev.turn_off()
	await dev.update()
	await get_dehumid_status()
	return

def get_ecoflow_soc():
    url = "http://raspberrypi.local:2112/metrics"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        metrics = response.text

        # Regex to match the line and extract the value (serial number not exposed)
        pattern = r'ecoflow_bms_master_f32_show_soc\{[^\}]*\}\s+([0-9.]+)'
        match = re.search(pattern, metrics)
        if match:
            return float(match.group(1))
        else:
            return 0
    except Exception:
        return 0
	
if __name__ == "__main__":
	asyncio.run(main())
