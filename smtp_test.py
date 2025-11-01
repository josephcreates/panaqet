import smtplib

HOST = "smtp.gmail.com"
PORT = 587
USERNAME = "lampteyjoseph860@gmail.com"
PASSWORD = "qmixazcxgwiqpseb"   # no spaces

try:
    s = smtplib.SMTP(HOST, PORT, timeout=10)
    s.ehlo()
    s.starttls()
    s.ehlo()
    s.login(USERNAME, PASSWORD)
    print("SMTP login successful")
    s.quit()
except Exception as e:
    print("SMTP login FAILED:", repr(e))
