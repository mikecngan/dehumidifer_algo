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
	on_counter = 0 #how long dehumidifier in 5 minute counters
	dehumid_flag = await get_dehumid_status()
	humidity_history = deque(maxlen=20)  # Store last 10 minutes
	avg_humidity = 100
	loop_counter = 0

	while True:
		bme280_data = retrieve_and_store(dehumid_flag) #retrieve and store sensor data for Grafana
		humidity_history.append(bme280_data.humidity)
		loop_counter += 1

		# Every 10 loops, check the dehumidifier status
		if loop_counter % 10 == 0:
			print("Regular 5 minute dehumidifier status update")
			dehumid_flag = await get_dehumid_status()
			if dehumid_flag == True:
				on_counter += 1
			else:
				on_counter = 0
				
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
			print("Battery life is high, setting aggressive mode")
			upper_bound = target_humidity + 2
			lower_bound = target_humidity - 3.5
			target_humidity = target_humidity - 1

		if bme280_data.humidity > upper_bound:
			up_counter = up_counter + 1

		if bme280_data.humidity < lower_bound:
			down_counter = down_counter + 1

		if battery_life > 95: # just be on cause battery is full
			if dehumid_flag == False:
				await dehumid_on()
			up_counter = 0
			down_counter = 0
			print("Counter reset, battery is full, dehumidifier on attempted")
			if dehumid_flag == False:
				dehumid_flag = await get_dehumid_status()
		elif up_counter > 10 and dehumid_flag == False:
			await dehumid_on()
			up_counter = 0
			down_counter = 0
			print("Up counter hit, Counter reset, dehumidifier on attempted")
			dehumid_flag = await get_dehumid_status()
		elif down_counter > 10 and dehumid_flag == True:
			await dehumid_off()
			up_counter = 0
			down_counter = 0
			print("Down counter hit, Counter reset, dehumidifier off attempted")
			dehumid_flag = await get_dehumid_status()
		#elif bme280_data.humidity >= avg_humidity and dehumid_flag == True and bme280_data.humidity < target_humidity and (len(humidity_history) == humidity_history.maxlen):
		#if humidity is not decreasing and dehumidifier is on for 3*5 minutes and algo has been running for awhile, turn it off
		elif bme280_data.humidity >= avg_humidity and on_counter > 3 and dehumid_flag == True and (len(humidity_history) == humidity_history.maxlen):
			await dehumid_off()
			up_counter = -20 # delay counter to prevent dehumidifier from turning on again for an additiona 10 minutes
			down_counter = 0
			on_counter = 0
			print("Counter reset, dehumidifier off attempted due to no humidity decrease")
			dehumid_flag = await get_dehumid_status()

		#print current values for debugging
		print("BEGIN DEBUGGING INFO")
		print(f"Humidity: {bme280_data.humidity}%, Up Counter: {up_counter}, Down Counter: {down_counter}, On Counter: {on_counter}, Avg Humidity: {avg_humidity}%, Dehumidifier Status: {dehumid_flag}")
		print(f"Upper Bound: {upper_bound}, Lower Bound: {lower_bound}, Target Humidity: {target_humidity}")
		print("--------------- END DEBUGGING INFO AND LOOP ---------------")
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
    try:
        dev = await Discover.discover_single("192.168.1.106")
        await dev.update()
        print("Dehumidier is on: " + str(dev.is_on))
        return dev.is_on
    except Exception as e:
        print(f"Error checking dehumidifier status: {e}")
        return False

async def dehumid_on():
    print("sending dehumidifer on signal")
    try:
        dev = await Discover.discover_single("192.168.1.106")
        await dev.turn_on()
        await dev.update()
        await get_dehumid_status()
    except Exception as e:
        print(f"Dehumidifier ON signal failed: {e}")
    return

async def dehumid_off():
    print("sending dehumidifer off signal")
    try:
        dev = await Discover.discover_single("192.168.1.106")
        await dev.turn_off()
        await dev.update()
        await get_dehumid_status()
    except Exception as e:
        print(f"Dehumidifier OFF signal failed: {e}")
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
