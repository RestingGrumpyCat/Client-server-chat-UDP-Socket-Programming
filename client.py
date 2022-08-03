import hashlib
import re
import socket
import os
import signal
import struct
import sys
import argparse
from urllib.parse import urlparse
import selectors
import time



# Constant for our buffer size
BUFFER_SIZE = 4096
MAX_STRING_SIZE = 2048



# Selector for helping us select incoming data from the server and messages typed in by the user.
sel = selectors.DefaultSelector()

#make sure registration msg is not print twice
registration =  False

# Signal handler for graceful exiting.  Let the server know when we're gone.
def signal_handler(sig, frame):
    global seq
    print('Interrupt received, shutting down ...')
    message=f'DISCONNECT {user} CHAT/1.0\n'
    wait_for_ack(client_socket, (host,port),message,seq)
    sys.exit(0)


#special function when  file bytes wait for ack
def waitAck(sock, addr, data, sequence):
    global seq
    ack_received = False
    packet = create_packet(sequence, len(data), data)
    while not ack_received:
        sock.sendto(packet, addr)
        try:
            sock.settimeout(2)
            received_packet, addr = sock.recvfrom(BUFFER_SIZE)
        except socket.timeout:
            pass
        else:
            unpacker = struct.Struct(f'I I {MAX_STRING_SIZE}s 32s')
            pack = unpacker.unpack(received_packet)
            received_sequence = pack[0]
            received_size = pack[1]
            received_data = pack[2]
            received_checksum = pack[3]
            values = (received_sequence, received_size, received_data)
            packer = struct.Struct(f'I I {MAX_STRING_SIZE}s')
            packed_data = packer.pack(*values)
            computed_checksum = bytes(hashlib.md5(packed_data).hexdigest(), encoding="UTF-8")
            if received_checksum == computed_checksum:
                received_text = received_data[:received_size].decode()
                if received_text == 'ACK':
                    ack_received = True
                    seq = 1 - seq


#general function to keep sending msg until received ask from server
def wait_for_ack(sock, addr, data, sequence):
    global seq, registration
    pre_msg = ''
    ack_received = False
    packet = create_packet(sequence, len(data.encode()), data.encode())
    while not ack_received:
        sock.sendto(packet, addr)
        try:
            sock.settimeout(2)
            received_packet, addr = sock.recvfrom(BUFFER_SIZE)
        except socket.timeout:
            pass
        else:
            #unpack packet if received one
            unpacker = struct.Struct(f'I I {MAX_STRING_SIZE}s 32s')
            pack = unpacker.unpack(received_packet)
            received_sequence = pack[0]
            received_size = pack[1]
            received_data = pack[2]
            received_checksum = pack[3]
            values = (received_sequence, received_size, received_data)
            packer = struct.Struct(f'I I {MAX_STRING_SIZE}s')
            packed_data = packer.pack(*values)
            computed_checksum = bytes(hashlib.md5(packed_data).hexdigest(), encoding="UTF-8")
            #make sure packet is not corrupted
            if received_checksum == computed_checksum:
                received_text = received_data[:received_size].decode()
                if received_text == pre_msg and received_text != 'ACK':
                    ack_received = True
                    seq = 1 - seq
                    if received_text == '200 Registration successful\n':
                        registration = True
                        print('Registration successful.  Ready for messaging!')


                if received_text == 'ACK':
                    ack_received = True
                    seq = 1 - seq
                else:
                    pre_msg = received_text

#compute checksum of passed data and create a packet
def create_packet(seq, size, data):
    tuple = (seq,size,data)
    packer = struct.Struct(f'I I {MAX_STRING_SIZE}s')
    packed_data = packer.pack(*tuple)
    computed_checksum = bytes(hashlib.md5(packed_data).hexdigest(), encoding="UTF-8")
    packet_tuple = (seq,size,data,computed_checksum)
    UDP_packet_structure = struct.Struct(f'I I {MAX_STRING_SIZE}s 32s')
    UDP_packet = UDP_packet_structure.pack(*packet_tuple)
    return UDP_packet

# Function to handle incoming messages from server.  Also look for disconnect messages to shutdown and messages for sending and receiving files.
def handle_message_from_server(sock, mask):
    global seq, registration
    received_packet, addr = sock.recvfrom(BUFFER_SIZE)
    unpacker = struct.Struct(f'I I {MAX_STRING_SIZE}s 32s')
    UDP_packet = unpacker.unpack(received_packet)
    received_sequence = UDP_packet[0]
    received_size = UDP_packet[1]
    received_data = UDP_packet[2]
    received_checksum = UDP_packet[3]
    values = (received_sequence, received_size, received_data)
    packer = struct.Struct(f'I I {MAX_STRING_SIZE}s')
    packed_data = packer.pack(*values)
    computed_checksum = bytes(hashlib.md5(packed_data).hexdigest(), encoding="UTF-8")
    #make sure packet is not corrupted
    if received_checksum == computed_checksum:
        #now send ack back to server
        ack_msg = 'ACK'.encode()
        if received_sequence == seq:
            ack_packet = create_packet(seq, len(ack_msg), ack_msg)
            sock.sendto(ack_packet, addr)
            seq = 1 - seq
            received_text = received_data[:received_size].decode()
            words = received_text.split(' ')
            # Handle server disconnection.
            if words[0] == 'DISCONNECT':
                t_end = time.time() + 0.05
                msg = 'ACK'.encode()
                ack_pack = create_packet(seq, len(msg), msg)
                while time.time() < t_end:
                    client_socket.sendto(ack_pack, (host, port))
                print('Disconnected from server ... exiting!')
                sys.exit(0)
            #if received registration response here, handle that as well
            elif re.match('\d{3}', words[0]):
                if words[0] != '200':
                    print('Error:  An error response was received from the server.  Details:\n')
                    print('Exiting now ...')
                    sys.exit(1)
                else:
                    if registration == False:
                        print('Registration successful.  Ready for messaging!')

            #communicate about file information and prepare for transfer
            elif words[0] == 'ATTACH':
                filename = words[1]
                if (os.path.exists(filename)):
                    filesize = os.path.getsize(filename)
                    header = f'Content-Length: {filesize} filename: {filename} origin: {user}\n'
                    wait_for_ack(client_socket, (host,port), header, seq)
                else:
                    header = f'Content-Length: -1 filename: {filename} origin: {user}\n'
                    wait_for_ack(client_socket, (host,port), header, seq)
           #ready to send file bytes
            elif words[0] == 'ATTACHMENT':
                filename = words[1]
                filesize = words[-1]
                counter = int(filesize)
                #read and send exact file bytes with no extra infomation
                print("This might take a while if the file is relatively big.")

                file_to_read = open(filename, 'rb')
                if counter < MAX_STRING_SIZE:
                    chunk = file_to_read.read(counter)
                    waitAck(sock, addr, chunk, seq)
                else:
                    while counter > 0:
                        if counter < MAX_STRING_SIZE:
                            chunk = file_to_read.read(counter)
                        else:
                            chunk = file_to_read.read(MAX_STRING_SIZE)
                        waitAck(sock, addr, chunk, seq)
                        counter = counter - MAX_STRING_SIZE
                    print(f"Finish sending file {filename}")
                    wait_for_ack(sock, addr, "File transferred", seq)

            else:
                print(received_text)
        else:
            processed_seq = 1 - seq
            ack_packet = create_packet(processed_seq, len(ack_msg), ack_msg)
            sock.sendto(ack_packet, addr)
    else:
        pass

#handle input from keyboard
def handle_keyboard_input(file, mask):
    line = sys.stdin.readline()
    message = f'@{user}: {line}'
    wait_for_ack(client_socket,(host,port),message,seq)


def main():
    global user
    global client_socket
    global host, port
    global seq

    #create client UDP socket
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Register our signal handler for shutting down.
    signal.signal(signal.SIGINT, signal_handler)

    # Check command line arguments to retrieve a URL.
    parser = argparse.ArgumentParser()
    parser.add_argument("user", help="user name for this user on the chat service")
    parser.add_argument("server", help="URL indicating server location in form of chat://host:port")
    parser.add_argument('-f', '--follow', nargs=1, default=[], help="comma separated list of users/topics to follow")
    args = parser.parse_args()

    # Check the URL passed in and make sure it's valid.  If so, keep track of
    # things for later.
    try:
        server_address = urlparse(args.server)
        if ((server_address.scheme != 'chat') or (server_address.port == None) or (server_address.hostname == None)):
            raise ValueError
        host = server_address.hostname
        port = server_address.port
    except ValueError:
        print('Error:  Invalid server.  Enter a URL of the form:  chat://host:port')
        sys.exit(1)
    user = args.user

    print('Connection to server established. Sending intro message...\n')

    seq = 0
    message = f'REGISTER {user} CHAT/1.0\n'
    wait_for_ack(client_socket, (host,port), "DUMMY", seq)
    wait_for_ack(client_socket,(host,port),message,seq)

    # Set up our selector.
    client_socket.setblocking(True)
    sel.register(client_socket, selectors.EVENT_READ, handle_message_from_server)
    sel.register(sys.stdin, selectors.EVENT_READ, handle_keyboard_input)

    # Now do the selection.

    while (True):
        events = sel.select()
        for key, mask in events:
            callback = key.data
            callback(key.fileobj, mask)

if __name__ == '__main__':
    main()
