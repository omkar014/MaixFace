import sensor, image, lcd, KPU as kpu, time, gc, utime, network, usocket, struct
from Maix import GPIO
from fpioa_manager import fm
from board import board_info
import face_storage

# WiFi
class wifi():
    nic = None
    def reset(force=False, reply=5, is_hard=True):
        if force == False and __class__.isconnected():
            return True
        try:
            fm.register(25, fm.fpioa.GPIOHS10) # cs
            fm.register(8, fm.fpioa.GPIOHS11)  # rst
            fm.register(9, fm.fpioa.GPIOHS12)  # rdy
            fm.register(28, fm.fpioa.SPI1_D0, force=True)   # mosi
            fm.register(26, fm.fpioa.SPI1_D1, force=True)   # miso
            fm.register(27, fm.fpioa.SPI1_SCLK, force=True) # sclk
            __class__.nic = network.ESP32_SPI(cs=fm.fpioa.GPIOHS10,
                rst=fm.fpioa.GPIOHS11, rdy=fm.fpioa.GPIOHS12, spi=1)
            print("ESP32_SPI firmware:", __class__.nic.version())
        except Exception as e:
            print("wifi.reset error:", e)
            return False
        return True
    def connect(ssid, pasw):
        if __class__.nic:
            return __class__.nic.connect(ssid, pasw)
    def isconnected():
        return __class__.nic and __class__.nic.isconnected()
    def ifconfig():
        return __class__.nic.ifconfig() if __class__.nic else None

# WiFi Configuration
SSID = "SSID"
PASW = "PASSWORD"
HTTP_HOST = "TRIGGER_SERVER_IP"
HTTP_PATH = "TRIGGER_SERVER_PATH"
CMD_SERVER = "ENROLL_SERVER_IP"
CMD_PATH = "ENROLL_SERVER_PATH"

def check_wifi_net(retry=5):
    if wifi.isconnected(): return True
    for _ in range(retry):
        try:
            wifi.reset(is_hard=True)
            print("try AT connect wifi...")
            wifi.connect(SSID, PASW)
            if wifi.isconnected(): break
        except Exception as e:
            print(e)
    print("WiFi:", wifi.isconnected(), wifi.ifconfig())
    return wifi.isconnected()

check_wifi_net()

# Global Variables
last_trigger_time = 0
record_face_time = 0
RECORD_COOLDOWN_MS = 10000
COOLDOWN_MS = 7777
last_face_time = utime.ticks_ms()
NO_FACE_TIMEOUT_MS = 10000
display_active = True
face_detected_start_time = 0
face_detected_stable = False
RECOGNITION_STABLE_MS = 777
WIFI_RECHECK_INTERVAL_MS = 600 * 1000
last_wifi_check_time = utime.ticks_ms()
last_enroll_time = 0
ENROLL_COOLDOWN_MS = 7000

# HTTP Trigger
def trigger_http():
    global last_trigger_time
    if utime.ticks_diff(utime.ticks_ms(), record_face_time) < RECORD_COOLDOWN_MS:
        return
    try:
        if wifi.isconnected():
            ai = usocket.getaddrinfo(HTTP_HOST, 80)
            addr = ai[0][-1]
            s = usocket.socket()
            s.setblocking(False)
            try:
                s.connect(addr)
            except OSError as e:
                if e.args[0] != 115:
                    s.close()
                    del (s)
                    gc.collect()
                    print("HTTP connect error:", e)
                    return
            req = "GET {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n".format(HTTP_PATH, HTTP_HOST)
            s.send(req)
            s.close()
            del (s)
            gc.collect()
            print("HTTP GET sent to", HTTP_HOST + HTTP_PATH)
            last_trigger_time = utime.ticks_ms()
        else:
            print("WiFi not connected or HTTP already triggered, skip HTTP")
    except Exception as e:
        print("HTTP send error:", e)

def poll_command():
    global last_enroll_time
    try:
        #print("[DEBUG] polling /cmd ...")
        ai = usocket.getaddrinfo(CMD_SERVER, 80)
        addr = ai[0][-1]
        s = usocket.socket()
        s.settimeout(1.5)
        s.connect(addr)
        req = "GET {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n".format(CMD_PATH, CMD_SERVER)
        #print("[DEBUG] sending:", req)
        s.send(req.encode())

        resp = b""
        while True:
            data = s.recv(128)
            if not data:
                break
            resp += data
        s.close()
        #print("[DEBUG] resp:", resp)

        if b"ENROLL" in resp:
            global start_processing, record_face_time
            now = utime.ticks_ms()
            if utime.ticks_diff(now, last_enroll_time) < ENROLL_COOLDOWN_MS:
                return
            last_enroll_time = now
            start_processing = True
            record_face_time = utime.ticks_ms()
            #print("[CMD] ENROLL triggered")

            try:
                ack = usocket.socket()
                ack.settimeout(0.3)
                ack.connect(usocket.getaddrinfo(CMD_SERVER, 80)[0][-1])
                request = "GET {}?eat=1 HTTP/1.1\r\nHost: {}\r\n\r\n".format(CMD_PATH, CMD_SERVER)
                ack.send(request.encode())
                ack.close()
                #print("[ACK] eat=1")
            except Exception as e:
                print("[ACK] Failed:", e)

    except Exception as e:
        print("poll error:", e)

# KPU Init
def init_kpu():
    global task_fd, task_ld, task_fe
    try:
        kpu.memtest()
        gc.collect()
        task_fd = kpu.load("/sd/FaceDetection.smodel")
        task_ld = kpu.load("/sd/FaceLandmarkDetection.smodel")
        task_fe = kpu.load("/sd/FeatureExtraction.smodel")
        print("Models loaded successfully")
    except Exception as e:
        print("Failed to load models:", e)
        raise

def deinit_kpu():
    global task_fd, task_ld, task_fe
    try:
        kpu.deinit(task_fe)
        kpu.deinit(task_ld)
        kpu.deinit(task_fd)
        task_fe = None
        task_ld = None
        task_fd = None
        gc.collect()
        kpu.memtest()
        print("Models deinitialized")
    except Exception as e:
        print("Failed to deinitialize models:", e)

# Models Init
try:
    init_kpu()
except Exception as e:
    print("Initialization failed:", e)
    raise

record_ftr = []
record_ftrs = []
names = []
record_ftrs_buf = []
face_storage.init("/sd/faces")
loaded = face_storage.load_all(record_ftrs, names)
record_ftrs_buf = []
for idx, f in enumerate(record_ftrs):
    try:
        fb = struct.pack("<" + "f" * len(f), *f)
        record_ftrs_buf.append(fb)
    except Exception as e:
        print("[DEBUG] pack loaded feature failed idx", idx, "err", e)
        try:
            record_ftrs.pop(idx)
            names.pop(idx)
        except Exception:
            pass

# LCD & Sensors Init
lcd.init()
lcd.rotation(2)
lcd.clear((0, 0, 0))
sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QVGA)
sensor.set_hmirror(1)
sensor.set_vflip(1)
sensor.run(1)
gc.collect()

# GPIO & Btns Init
fm.register(board_info.BOOT_KEY, fm.fpioa.GPIOHS0)
key_gpio = GPIO(GPIO.GPIOHS0, GPIO.IN)
start_processing = False
last_key_time = 0
BOUNCE_PROTECTION = 50

def set_key_state(*_):
    global start_processing, record_face_time, last_key_time
    now = utime.ticks_ms()
    if utime.ticks_diff(now, last_key_time) < BOUNCE_PROTECTION:
        return
    last_key_time = now
    start_processing = True
    record_face_time = utime.ticks_ms()

key_gpio.irq(set_key_state, GPIO.IRQ_RISING, GPIO.WAKEUP_NOT_SUPPORT)

# Face Recognition Config
anchor = (1.889, 2.5245, 2.9465, 3.94056, 3.99987, 5.3658, 5.155437, 6.92275, 6.718375, 9.01025)
dst_point = [(44, 59), (84, 59), (64, 82), (47, 105), (81, 105)]
a = kpu.init_yolo2(task_fd, 0.5, 0.3, 5, anchor)

img_lcd = image.Image()
img_face = image.Image(size=(128, 128))
a = img_face.pix_to_ai()
gc.enable()
gc.threshold(8192)
ACCURACY = 81
frame_count = 0
clock = time.clock()

# ---------------- Main Loop ----------------
try:
    while True:
        frame_count += 1
        if frame_count % 10 == 0:
            gc.collect()
            gc.threshold(8192)
        if frame_count % 100 == 0:
            poll_command()
        img = sensor.snapshot()
        clock.tick()
        code = kpu.run_yolo2(task_fd, img)
        if code:
            last_face_time = utime.ticks_ms()
            display_active = True
            for i in code:
                a = img.draw_rectangle(i.rect())
                face_cut = img.cut(i.x(), i.y(), i.w(), i.h())
                face_cut_128 = face_cut.resize(128, 128)
                a = face_cut_128.pix_to_ai()
                fmap = kpu.forward(task_ld, face_cut_128)
                plist = fmap[:]
                le = (i.x() + int(plist[0] * i.w() - 10), i.y() + int(plist[1] * i.h()))
                re = (i.x() + int(plist[2] * i.w()), i.y() + int(plist[3] * i.h()))
                nose = (i.x() + int(plist[4] * i.w()), i.y() + int(plist[5] * i.h()))
                lm = (i.x() + int(plist[6] * i.w()), i.y() + int(plist[7] * i.h()))
                rm = (i.x() + int(plist[8] * i.w()), i.y() + int(plist[9] * i.h()))
                a = img.draw_circle(le[0], le[1], 4)
                a = img.draw_circle(re[0], re[1], 4)
                a = img.draw_circle(nose[0], nose[1], 4)
                a = img.draw_circle(lm[0], lm[1], 4)
                a = img.draw_circle(rm[0], rm[1], 4)
                src_point = [le, re, nose, lm, rm]
                T = image.get_affine_transform(src_point, dst_point)
                a = image.warp_affine_ai(img, img_face, T)
                a = img_face.ai_to_pix()
                del(face_cut_128)
                del (face_cut)
                gc.collect()
                fmap = kpu.forward(task_fe, img_face)
                feature = kpu.face_encode(fmap[:])
                #print("[DEBUG] face_encode len =", len(feature))

                try:
                    feature_buf = struct.pack("<" + "f" * len(feature), *feature)
                except Exception as e:
                    #print("[DEBUG] pack feature failed:", e)
                    feature_buf = None

                scores = []
                if feature_buf is not None:
                    for j in range(len(record_ftrs_buf)):
                        try:
                            score = kpu.face_compare(record_ftrs_buf[j], feature_buf)
                            scores.append(score)
                        except Exception as e:
                            print("[DEBUG] face_compare failed idx", j, "err", e)
                            # Remove Errors
                            # try:
                            #     record_ftrs.pop(j); record_ftrs_buf.pop(j); names.pop(j)
                            # except: pass
                            continue
                else:
                    scores = []
                max_score = 0
                index = 0
                for k in range(len(scores)):
                    if max_score < scores[k]:
                        max_score = scores[k]
                        index = k
                if max_score > ACCURACY:
                    a = img.draw_string(i.x(), i.y(), "%s :%.1f" % (names[index], max_score), color=(0, 255, 0), scale=2)
                    now = utime.ticks_ms()

                    if face_detected_start_time == 0:
                        face_detected_start_time = now
                        face_detected_stable = False
                    else:
                        if not face_detected_stable and utime.ticks_diff(now, face_detected_start_time) >= RECOGNITION_STABLE_MS:
                            face_detected_stable = True

                            if utime.ticks_diff(now, last_trigger_time) > COOLDOWN_MS:
                                trigger_http()
                else:
                    a = img.draw_string(i.x(), i.y(), "X :%.1f" % max_score, color=(255, 0, 0), scale=2)
                    face_detected_start_time = 0
                    face_detected_stable = False
                if start_processing:
                    if not code:
                        start_processing = False
                        continue
                    record_ftr = tuple(feature)
                    record_ftrs.append(record_ftr)
                    try:
                        fb = struct.pack("<" + "f" * len(record_ftr), *record_ftr)
                        record_ftrs_buf.append(fb)
                    except Exception as e:
                        print("[DEBUG] pack saved feature failed:", e)
                        record_ftrs_buf.append(None)

                    new_name = face_storage.save_new_face(record_ftr)
                    if new_name:
                        names.append(new_name)
                    else:
                        print("[Warning] face saved failed or name not returned")
                    print("Added face, total:", len(record_ftrs))
                    start_processing = False
                break
        else:
            face_detected_stable = False
            face_detected_start_time = 0
            now = utime.ticks_ms()
            if utime.ticks_diff(now, last_face_time) > NO_FACE_TIMEOUT_MS:
                display_active = False

        now = utime.ticks_ms()
        if utime.ticks_diff(now, last_wifi_check_time) > WIFI_RECHECK_INTERVAL_MS:
            last_wifi_check_time = now
            if not wifi.isconnected():
                try:
                    wifi.reset(force=True)
                    wifi.connect(SSID, PASW)
                    if wifi.isconnected():
                        print("[WiFi] Reconnected:", wifi.ifconfig())
                    else:
                        print("[WiFi] Reconnect failed.")
                except Exception as e:
                    print("[WiFi] Reconnect error:", e)

        if display_active:
            # FPS Display
            #fps = clock.fps()
            #img.draw_string(220, 0, "FPS: %.1f" % fps, color=(255, 255, 255), scale=2)

            img.draw_string(0, 0, "Status: ", color=(255, 255, 255), scale=1.5)
            img.draw_string(65, 0, "True" if face_detected_stable else "False", color=(0, 255, 0) if face_detected_stable else (255, 0, 0), scale=1.5)
            img.draw_string(170, 225, "LUNGMEN ELECTRONICS", color=(76, 153, 0), scale=1.3)
            a = lcd.display(img)
        else:
            lcd.clear((0, 0, 0))
        del (img)
        gc.collect()

except KeyboardInterrupt:
    print("Program interrupted, cleaning up...")
    deinit_kpu()
    lcd.clear((0, 0, 0))
    lcd.deinit()
    sensor.reset()
    gc.collect()
    print("Cleanup completed")

except Exception as e:
    print("Error occurred:", e)
    deinit_kpu()
    lcd.clear((0, 0, 0))
    lcd.deinit()
    sensor.reset()
    gc.collect()
    print("Cleanup completed")

# LUNGMEN ELECTRONICS 2025
