import asyncio

import smbus2
import bme280
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
import time

from kasa import Discover
from collections import deque
import statistics

port = 1
address = 0x76
bus = smbus2.SMBus(port)

target_humidity = 50

async def main():
	up_counter = 0
	down_counter = 0
	dehumid_flag = True
	pressure_humidity = deque(maxlen=20)  # Store last 10 minutes

	while True:
		bme280_data = retrieve_and_store() #retrieve and store sensor data for Grafana
		pressure_humidity.append(bme280_data.humidity)
		
		# Check if we have enough history to calculate average
		if len(pressure_humidity) == pressure_humidity.maxlen:
			avg_humidity = statistics.mean(pressure_humidity)
			
		
		#run algo to decide if dehumidifier should be on
		if bme280_data.pressure > target_humidity + 2:
			up_counter = up_counter + 1

		if bme280_data.pressure < target_humidity - 2:
			down_counter = down_counter + 1

		if up_counter > 10 and dehumid_flag == False:
			await dehumid_on()
			up_counter = 0
			down_counter = 0
		elif down_counter > 10 and dehumid_flag == True:
			await dehumid_off()
			up_counter = 0
			down_counter = 0
		elif bme280_data.humidity >= avg_humidity and dehumid_flag == True:
			await dehumid_off()
			up_counter = 0
			down_counter = 0

		time.sleep(30)
	return
	
def retrieve_and_store():
	calibration_params = bme280.load_calibration_params(bus, address)
	data = bme280.sample(bus, address, calibration_params)

	# Prometheus setup
	registry = CollectorRegistry()
	g_temp = Gauge('sensor_temperature_celsius', 'Temperature in Celsius', registry=registry)
	g_press = Gauge('sensor_pressure_hpa', 'Pressure in hPa', registry=registry)
	g_hum = Gauge('sensor_humidity_percent', 'Humidity in %', registry=registry)

	g_temp.set(data.temperature)
	g_press.set(data.pressure)
	g_hum.set(data.humidity)

	push_to_gateway('localhost:9091', job='bme280_sensor', registry=registry)

	# Optional: print values
	print(f"Timestamp: {data.timestamp}")
	print(f"Humidity: {data.humidity} %H")

	return data

async def dehumid_on():
	print("dehumidifer on")
	dev = await Discover.discover_single("192.168.1.107")
	await dev.turn_on()
	await dev.update()
	print(await dev.update())
	return

async def dehumid_off():
	print("dehumidifer off")
	dev = await Discover.discover_single("192.168.1.107")
	await dev.turn_off()
	await dev.update()
	print(await dev.update())
	return
	
if __name__ == "__main__":
	asyncio.run(main())
