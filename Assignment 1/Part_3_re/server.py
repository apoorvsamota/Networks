import socket
import json
import threading
import queue

with open ("config.json", "r") as f:
    config = json.load(f)

IP = config["server_ip"]
PORT = config["server_port"]
k = config["k"]
p = config["p"]
filename = config["filename"]
iterations = config["num_iterations"]
words = []
with open(filename, "r") as f:
    words = f.read().strip().split(',')

myq = queue.Queue()

def q():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((IP, PORT))
    server.listen()
    while True:
        myq.put(server.accept())


def processor():
    while True:
        cnctSocket, clientip = myq.get()
        x = cnctSocket.recv(1024).decode()
        print(x)
        reply= ""
        n = len(words)
        if ( p >= len(words)):
            reply = "EOF\n"
        else:
            for i in range(p, min(p+k, n)):
                reply += words[i]
                if (i != min(p+k-1, n-1)):
                    reply += ","
            if ( p + k > n) :
                reply += ",EOF"
            reply += "\n"
        cnctSocket.send(reply.encode())
        
    return

t1 = threading.Thread(target=q)
t2 = threading.Thread(target= processor)
t1.start()
t2.start()