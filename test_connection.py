import socket

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "Unable to get IP"

print(f"[IP] Laptop IP: {get_ip()}")
print("Server URL: http://{}:5000".format(get_ip()))
print("Open this on your phone browser")