# -*- coding: utf-8 -*-

from functools import wraps
import sys
import time
import os.path
from datetime import datetime, timedelta
import yaml
import json
import requests
import threading

import board
import busio
import adafruit_sgp30
from ltr559 import LTR559
import ST7789

from PIL import ImageFont, ImageDraw, Image

import logging
from telegram.ext import Updater, CommandHandler, Filters

from Adafruit_IO import Client

# Load configuration file
config = None
with open('config.yaml') as file:
    config = yaml.full_load(file)

logging.basicConfig(filename='logs/python.txt')

# Set up CO2 & VOC sensor
i2c = busio.I2C(board.SCL, board.SDA, frequency=100000)
sgp30 = adafruit_sgp30.Adafruit_SGP30(i2c)

# Set up light and proximity sensor
ltr559 = LTR559()

# Set up screen
disp = ST7789.ST7789(
    port=0,
    cs=ST7789.BG_SPI_CS_FRONT,  # BG_SPI_CSB_BACK or BG_SPI_CS_FRONT
    dc=9,
    backlight=19,               # 18 for back BG slot, 19 for front BG slot.
    spi_speed_hz=80 * 1000 * 1000
)
WIDTH = disp.width
HEIGHT = disp.height


def turn_off_display():
    disp.set_backlight(0)


def turn_on_display():
    disp.set_backlight(1)


# Initialize display.
disp.begin()

# Initialize Telegram
updater = Updater(
    token=config['telegram']['token'], use_context=True)
dispatcher = updater.dispatcher
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

# Restrict to certain Telegram users
# https://github.com/python-telegram-bot/python-telegram-bot/wiki/Code-snippets#restrict-access-to-a-handler-decorator


def restricted(func):
    @wraps(func)
    def wrapped(update, context, *args, **kwargs):
        global config
        user_id = update.effective_user.id
        if user_id not in config['telegram']['authorized_user_ids']:
            print("Unauthorized access denied for {}".format(user_id))
            return
        return func(update, context, *args, **kwargs)
    return wrapped


@restricted
def start(update, context):
    tg_message = ""
    if sgp30.air_quality and iqair_aqi is not None:
        if sgp30.air_quality == 'bad':
            if iqair_aqi > 100:
                tg_message += "\nLa calidad del aire tanto dentro como fuera de casa es muy mala. Habrá que aguantarse. 😷"
            elif iqair_aqi > 50:
                tg_message += "\nHuele a tigre. Aunque la calidad del aire exterior no es muy buena, quizá sea oportuno ventilar un poco. 🔥"
            else:
                tg_message += "\nHuele a tigre. Haz el favor de ventilar. 🔥"

        if sgp30.air_quality == 'medium':
            if iqair_aqi > 100:
                tg_message += "\nAunque vendría bien ventilar un poco, la calidad del aire fuera de casa es muy mala. 💔"
            elif iqair_aqi > 50:
                tg_message += "\nLa calidad del aire tanto dentro como fuera de casa es bastante mala. Habrá que aguantarse. 😷"
            else:
                tg_message += "\nEl ambiente está un poco cargado. No nos vendría mal ventilar 🏡"

        if sgp30.air_quality == 'good':
            if iqair_aqi > 100:
                tg_message += "\nLa calidad del aire es muy mala afuera, pero muy buena adentro. Hoy es mejor quedarse en casa y no ventilar. 🛋"
            if iqair_aqi > 50:
                tg_message += "\nLa calidad del aire es mala afuera, pero muy buena adentro. Hoy es mejor no ventilar. 🛋"
            else:
                tg_message += "\nQué aire más limpio 💖"

        if sgp30.eCO2 == 400:
            tg_message += "\nCO2: <400 ppm, VOC: {} ppb, AQI: {}".format(
                sgp30.TVOC, iqair_aqi)
        else:
            tg_message += "\nCO2: {} ppm, VOC: {} ppb, AQI: {}".format(
                sgp30.eCO2, sgp30.TVOC, iqair_aqi)
    else:
        tg_message += "\nTodavía estoy poniéndome en marcha, así que no tengo datos aún"

    context.bot.send_message(
        chat_id=update.effective_chat.id, text=tg_message)


start_handler = CommandHandler('start', start)
dispatcher.add_handler(start_handler)
updater.start_polling()

# Load emoji while starts
image = Image.open('assets/emoji-fire.png')
disp.display(image)

# Calcifer says hi
print("🔥 Calcifer is waking up, please wait...")
# print("SGP30 serial #", [hex(i) for i in sgp30.serial])


def calcifer_expressions(expression):
    if expression == 'talks':
        image = Image.open('assets/calcifer-talks.gif')
    elif expression == 'idle':
        image = Image.open('assets/calcifer-idle.gif')
    elif expression == 'rawr':
        image = Image.open('assets/calcifer-rawr.gif')
    frame = 0
    while frame < image.n_frames:
        try:
            image.seek(frame)
            disp.display(image.resize((WIDTH, HEIGHT)))
            frame += 1
            time.sleep(0.05)
        except EOFError:
            frame = 0

# Air quality levels
# From Hong Kong Indoor Air Quality Management Group
# https://www.iaq.gov.hk/media/65346/new-iaq-guide_eng.pdf


def air_quality():
    global sgp30
    if sgp30:
        if sgp30.eCO2 and sgp30.TVOC:
            if sgp30.eCO2 > 1000 or sgp30.TVOC > 261:
                sgp30.air_quality = "bad"
            elif sgp30.eCO2 > 800 or sgp30.TVOC > 87:
                sgp30.air_quality = "medium"
            else:
                sgp30.air_quality = "good"
        else:
            sgp30.air_quality = "unknown"


screen_timeout = 0
start_time = datetime.now()

# Initialise air quality sensor
sgp30.iaq_init()

# Load air quality sensor baseline from config file
baseline_eCO2_restored, baseline_TVOC_restored, baseline_timestamp = None, None, None
if config['sgp30_baseline']['timestamp'] is not None:
    baseline_timestamp = config['sgp30_baseline']['timestamp']

    # Ignore stored baseline if older than a week
    if datetime.now() < baseline_timestamp + timedelta(days=7):
        baseline_eCO2_restored = config['sgp30_baseline']['eCO2']
        baseline_TVOC_restored = config['sgp30_baseline']['TVOC']

        print('Stored baseline is recent enough: 0x{:x} 0x{:x} {}'.format(
            baseline_eCO2_restored, baseline_TVOC_restored, baseline_timestamp))

        # Set baseline
        sgp30.set_iaq_baseline(
            baseline_eCO2_restored, baseline_TVOC_restored)
    else:
        print('Stored baseline is too old')

result_log = 'logs/sgp30-result.txt'
baseline_log = 'logs/sgp30-baseline.txt'
baseline_log_counter = datetime.now() + timedelta(minutes=10)

# If there are not baseline values stored, wait 12 hours before saving every hour
if baseline_eCO2_restored is None or baseline_TVOC_restored is None:
    baseline_log_counter_valid = datetime.now() + timedelta(hours=12)
    print('Calcifer will store a valid baseline in 12 hours')
else:
    baseline_log_counter_valid = datetime.now() + timedelta(hours=1)

# External air quality provided by AirVisual (IQAir)
# Based on US EPA National Ambient Air Quality Standards https://support.airvisual.com/en/articles/3029425-what-is-aqi
# <50, Good; 51-100, Moderate (ventilation is discouraged); >101, Unhealthy

iqair_query = 'https://api.airvisual.com/v2/nearest_city?lat={}&lon={}&key={}'.format(
    config['location']['latitude'], config['location']['longitude'], config['iqair']['token'])
iqair_result = None
iqair_aqi = None


def update_iqair_result():
    global iqair_result, iqair_query, iqair_aqi
    threading.Timer(1800.0, update_iqair_result).start()
    iqair_result = requests.get(iqair_query)
    iqair_result = iqair_result.json()
    if iqair_result['status'] == 'success':
        iqair_aqi = iqair_result['data']['current']['pollution']['aqius']
        print("Outdoors air quality: AQI {} | {}".format(
            iqair_aqi, iqair_result['data']['current']['pollution']['ts']))
    return


update_iqair_result()

# Send data to Adafruit IO
aio = Client(config['adafruit']['username'], config['adafruit']['key'])


def send_to_adafruit_io():
    global aio, sgp30
    aio_eCO2 = aio.feeds('eco2')
    aio_TVOC = aio.feeds('tvoc')
    aio.send_data(aio_eCO2.key, sgp30.eCO2)
    aio.send_data(aio_TVOC.key, sgp30.TVOC)
    print("Readings sent to Adafruit IO")
    threading.Timer(30.0, send_to_adafruit_io).start()


def send_to_adafruit_io_run():
    global aio, sgp30
    threading.Timer(30.0, send_to_adafruit_io).start()


send_to_adafruit_io_run()

# Wait while sensor warms up
warmup_counter = datetime.now() + timedelta(seconds=30)
while datetime.now() < warmup_counter:
    if sgp30.eCO2 > 400 and sgp30.TVOC > 0:
        break
    time.sleep(1)

while True:
    air_quality()

    # Get proximity
    ltr559.update_sensor()
    lux = ltr559.get_lux()
    prox = ltr559.get_proximity()
    # print("Lux: {:06.2f}, Proximity: {:04d}".format(lux, prox))

    # Get air quality
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result_human = 'CO2: {} ppm, VOC: {} ppb | {}'.format(
        sgp30.eCO2, sgp30.TVOC, current_time_str)
    print(result_human)

    # Log baseline
    baseline_human = 'CO2: {0} 0x{0:x}, VOC: {1} 0x{1:x} | {2}'.format(
        sgp30.baseline_eCO2, sgp30.baseline_TVOC, current_time_str)

    if datetime.now() > baseline_log_counter:
        with open(result_log, 'a') as file:
            file.write(result_human + '\n')

    if datetime.now() > baseline_log_counter_valid:
        baseline_log_counter_valid = datetime.now() + timedelta(hours=1)
        print("Valid baseline: " + baseline_human)
        with open(baseline_log, 'a') as file:
            file.write("Valid: " + baseline_human + '\n')

        # Store new valid baseline
        config['sgp30_baseline']['eCO2'] = sgp30.baseline_eCO2
        config['sgp30_baseline']['TVOC'] = sgp30.baseline_TVOC
        config['sgp30_baseline']['timestamp'] = datetime.now()

        with open('config.yaml', 'w') as file:
            yaml.dump(config, file)
            print('Baseline updated on config file')

    elif datetime.now() > baseline_log_counter:
        baseline_log_counter = datetime.now() + timedelta(minutes=10)

        print("Baseline: " + baseline_human)
        with open(baseline_log, 'a') as file:
            file.write(baseline_human + '\n')

    # Alerts
    if prox >= 5 or screen_timeout > 0:
        if prox >= 5:
            screen_timeout = 5  # seconds the screen will stay on
        screen_timeout -= 1

        turn_on_display()

        color = (255, 255, 255)
        background_color = (0, 0, 0)
        if sgp30.air_quality == "bad":
            background_color = (255, 0, 0)
        elif sgp30.air_quality == "medium":
            color = (0, 0, 0)
            background_color = (255, 255, 0)

        if background_color != (0, 0, 0):
            img = Image.new('RGB', (WIDTH, HEIGHT), color=background_color)
        else:
            img = Image.open('assets/background.png')

        draw = ImageDraw.Draw(img)

        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
        font_bold = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)

        draw.rectangle((0, 0, disp.width, 80), background_color)

        draw.text((10, 10), 'CO2', font=font, fill=color)
        if (sgp30.eCO2 <= 400):
            draw.text((10, 45), '<400', font=font_bold, fill=color)
        else:
            draw.text((10, 45), str(sgp30.eCO2),
                      font=font_bold, fill=color)
        draw.text((10, 80), 'ppm', font=font, fill=color)

        draw.text((125, 10), 'VOC', font=font, fill=color)
        draw.text((125, 45), str(sgp30.TVOC),
                  font=font_bold, fill=color)
        draw.text((125, 80), 'ppb', font=font, fill=color)

        disp.display(img)
    else:
        turn_off_display()

    time.sleep(1.0)
