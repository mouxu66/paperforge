"""探测一个空闲端口（8770 -> 8771 -> ... -> 8774）并打印到 stdout。

供 .bat 调用：python scripts/find_port.py > port.txt
读取到的就是后端要使用的端口号。
"""
import socket

for port in (8770, 8771, 8772, 8773, 8774):
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        try:
            s.close()
        except Exception:
            pass
        continue
    s.close()
    print(port)
    break
else:
    print(8770)
