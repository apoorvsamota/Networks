# import socket
# import json
# import argparse
# parser = argparse.ArgumentParser()
# parser.add_argument("--c", type = int, required = True)
# c = parser.parse_args().c

# with open ("config2.json", "r") as f:
#     config = json.load(f)

# IP = config["server_ip"]
# PORT = config["server_port"]
# k = config["k"]
# p = config["p"]
# filename = config["filename"]
# iterations = config["num_iterations"]

# client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# print("trying to connect to ", IP, PORT)
# client.connect((IP, PORT))
# mystr = f"{p},{k}\n".encode()
# for i in range(c): client.send(mystr)
# reply = client.recv(1024).decode().strip().split(',')
# mydict ={}
# for i in reply:
#     if i not in mydict: mydict[i]=0
#     mydict[i]+=1
# for i in mydict.keys():
#     print(i, mydict[i])
# client.close()

import socket
import json
import argparse
import time

argument = argparse.ArgumentParser()
argument.add_argument('--config', type=str, default='config.json', help='Path to the JSON config file')
# parser = argparse.ArgumentParser()
argument.add_argument("--c", type = int, required = True)

args = argument.parse_args()
config_file = args.config
c = args.c

with open (config_file, "r") as f:
    config = json.load(f)

IP = config["server_ip"]
PORT = config["server_port"]
k = config["k"]
p = config["p"]
filename = config["filename"]
iterations = config["num_iterations"]
mydict ={}
flag = False
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
client.connect((IP, PORT))
# print("connected")
start_time = time.time()
# print("lmao")
while (not flag):
    # print("trying to connect to ", IP, PORT)
    for _ in range(c):
        mystr = f"{p},{k}\n".encode()
        client.send(mystr)
        p+=k
        # time.sleep(0.001)
    # print("sent, ", mystr)
    print("sent and waiting now", int(1000*(time.time()-start_time)))
    replies =0
    buffer =""
    while(replies<c):
        replyall = client.recv(1024).decode()
        buffer += replyall
        print(buffer, int(1000*(time.time()-start_time)))

        while("\n" in buffer):
            reply, buffer = buffer.split('\n', 1)
            final = reply.split(',')
            for i in final:
                if i=="EOF":
                    flag = True
                    continue
                if i not in mydict: mydict[i]=0
                mydict[i]+=1
            replies+=1
            # if not flag:
            #     mystr = f"{p},{k}\n".encode()
            #     client.send(mystr)
            #     p+=k
            #     replies-=1
    

client.close()


for i in mydict.keys():
    print(i, mydict[i])
print("ELAPSED_MS:", int(1000*(time.time()-start_time)))


