import hashlib
import re
import socket
import os
import time
import signal
import struct
import sys
import selectors

# Constant for our buffer size
BUFFER_SIZE = 4096
MAX_STRING_SIZE = 2048

# Selector for helping us select incoming data and connections from multiple sources.

sel = selectors.DefaultSelector()

# Client list for mapping connected clients to their connections.
client_list = []

# Global vairble for handling edge cases.
readyToReceiveFile = False
disconnected = False
bytes_written = 0
bytes_list = []
file_header = ''

# Signal handler for graceful exiting.  We let clients know in the process so they can disconnect too.
def signal_handler(sig, frame):
    global seq
    print('Interrupt received, shutting down ...')
    message='DISCONNECT CHAT/1.0\n'
    for reg in client_list:
        wait_for_ack(server_socket,reg[1],message,seq)
    sys.exit(0)

# Add a user to the client list.
def client_add(user, addr, follow_terms):
    registration = (user, addr, follow_terms)
    client_list.append(registration)

# Remove a client when disconnected.
def client_remove(user):
    for reg in client_list:
        if reg[0] == user:
            client_list.remove(reg)
            break

# Function to list clients.
def list_clients():
    first = True
    list = ''
    for reg in client_list:
        if first:
            list = reg[0]
            first = False
        else:
            list = f'{list}, {reg[0]}'
    return list

# Function to return list of followed topics of a user.
def client_follows(user):
    for reg in client_list:
        if reg[0] == user:
            first = True
            list = ''
            for topic in reg[2]:
                if first:
                    list = topic
                    first = False
                else:
                    list = f'{list}, {topic}'
            return list
    return None

# Search the client list for a particular user.
def client_search(user):
    for reg in client_list:
        if reg[0] == user:
            return reg[1]
    return None


# Function to add to list of followed topics of a user, returning True if added or False if topic already there.
def client_add_follow(user, topic):

    for reg in client_list:
        if reg[0] == user:
            if topic in reg[2]:
                return False
            else:
                reg[2].append(topic)
                return True
    return None

# Function to remove from list of followed topics of a user, returning True if removed or False if topic was not already there.
def client_remove_follow(user, topic):
    for reg in client_list:
        if reg[0] == user:
            if topic in reg[2]:
                reg[2].remove(topic)
                return True
            else:
                return False
    return None

# Remove a client when disconnected.
def client_remove(user):
    for reg in client_list:
        if reg[0] == user:
            client_list.remove(reg)
            break

# Search the client list for a particular user by their socket.
def client_search_by_addr(addr):
    for reg in client_list:
        if reg[1] == addr:
            return reg[0]
    return None

#compute chucksum of passed data and create a packet
def create_packet(seq, size, data):
    tuple = (seq,size,data)
    packer = struct.Struct(f'I I {MAX_STRING_SIZE}s')
    packed_data = packer.pack(*tuple)
    computed_checksum = bytes(hashlib.md5(packed_data).hexdigest(), encoding="UTF-8")
    packet_tuple = (seq,size,data,computed_checksum)
    UDP_packet_structure = struct.Struct(f'I I {MAX_STRING_SIZE}s 32s')
    UDP_packet = UDP_packet_structure.pack(*packet_tuple)
    return UDP_packet

#keep sending data to receiver until receive ack
def wait_for_ack(sock, addr, data, sequence):
    global seq
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
            #unpack data and check if it is corrupted
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
                try:
                    received_text = received_data[:received_size].decode()
                except (UnicodeDecodeError, AttributeError):
                    #this only happens when accidentally receives file bytes
                    #so select handle_file_transfer to take care of the following
                    if readyToReceiveFile == True:
                        ack_received = True
                        seq = 1 - seq
                        sel.unregister(sock)
                        sel.register(sock, selectors.EVENT_READ, handle_file_transfer)

                else:
                    #if ack package lost, make sure the code does not get caught in the loop
                    if received_text == pre_msg and received_text != 'ACK':
                        ack_received = True
                        seq = 1 - seq

                    if received_text == 'ACK':
                        ack_received = True
                        seq = 1 - seq
                    else:
                        pre_msg = received_text


#funtion used to receive file bytes
def handle_file_transfer(sock,mask):
    global seq
    global file_header
    global bytes_written

    #get file information
    header_parts = file_header.split(' ')
    filename = header_parts[1]
    filesize = header_parts[-1]

    #start receiving file bytes packet
    received_packet, addr = sock.recvfrom(BUFFER_SIZE)
    unpacker = struct.Struct(f'I I {MAX_STRING_SIZE}s 32s')
    UDP_packet = unpacker.unpack(received_packet)
    received_sequence = UDP_packet[0]
    received_size = UDP_packet[1]
    received_data = UDP_packet[2]
    received_checksum = UDP_packet[3]
    #if packet not corrupted
    if check(received_sequence, received_size, received_data, received_checksum) == True:
        #we send ack back with corresponding seq number
        ack_msg = 'ACK'
        if received_sequence == seq:
            ack_packet = create_packet(seq, len(ack_msg), ack_msg.encode())
            sock.sendto(ack_packet, addr)
            seq = 1 - seq
        else:
            processed_seq = 1 - seq
            ack_packet = create_packet(processed_seq, len(ack_msg), ack_msg.encode())
            sock.sendto(ack_packet, addr)
        #append all the unique packet data to a list
        bytes_to_write = int(filesize)
        if bytes_written < bytes_to_write:
            if (bytes_to_write - bytes_written) < MAX_STRING_SIZE:
                received_data = received_data[:received_size]
            if received_data not in bytes_list:
                bytes_list.append(received_data)
                bytes_written = bytes_written + len(received_data)
            else:
                pass
        else:
            #once finish receiving file, select read_message again
            sel.unregister(sock)
            sel.register(sock, selectors.EVENT_READ, read_message)
        #write all the unique data in the list to a file
        file_to_write = open(filename, 'wb')
        for elem in bytes_list:
            file_to_write.write(elem)
    else:
        pass

#function to read message from client
def read_message(sock, mask):
    global readyToReceiveFile
    global seq
    global file_header
    #receive packet of pre-defined buffer size
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
    #make sure the packet is not corrupted
    if received_checksum == computed_checksum:
        ack_msg = 'ACK'.encode()
        #send ack back with corresponding seq number
        if received_sequence == seq:
            ack_packet = create_packet(seq, len(ack_msg), ack_msg)
            sock.sendto(ack_packet, addr)
            seq = 1 - seq
            received_text = received_data[:received_size].decode()
            message_parts = received_text.split()
            user = client_search_by_addr(addr)
            #make sure msg received is not empty
            if len(message_parts) > 0:
                # handle disconnect request
                if message_parts[0] == 'DISCONNECT':
                    print('Disconnecting user ' + user)
                    client_remove(user)
                    response = 'DISCONNECT CHAT/1.0\n'
                    wait_for_ack(sock, addr, response, seq)
                    seq = 1 - seq
                    sel.unregister(sock)
                    sel.register(server_socket, selectors.EVENT_READ, accept_client)
                #handle individual commands with no following parameters
                elif ((len(message_parts) == 2) and ((message_parts[1] == '!list') or (message_parts[1] == '!exit') or (
                        message_parts[1] == '!follow?'))):
                    if message_parts[1] == '!list':
                        response = list_clients() + '\n'
                        wait_for_ack(sock, addr, response, seq)

                    elif message_parts[1] == '!exit':
                        print('Disconnecting user ' + user)
                        client_remove(user)
                        response = 'DISCONNECT CHAT/1.0\n'
                        wait_for_ack(sock, addr, response, seq)
                        seq = 1 - seq
                        sel.unregister(sock)
                        sel.register(server_socket, selectors.EVENT_READ, accept_client)

                    elif message_parts[1] == '!follow?':
                        response = client_follows(user) + '\n'
                        wait_for_ack(sock, addr, response, seq)

                # Check for specific commands with a parameter.
                elif ((len(message_parts) == 3) and ((message_parts[1] == '!follow') or (message_parts[1] == '!unfollow'))):
                    if message_parts[1] == '!follow':
                        topic = message_parts[2]
                        if client_add_follow(user, topic):
                            response = f'Now following {topic}\n'
                        else:
                            response = f'Error:  Was already following {topic}\n'
                        wait_for_ack(sock, addr, response, seq)

                    elif message_parts[1] == '!unfollow':
                        topic = message_parts[2]
                        if topic == '@all':
                            response = 'Error:  All users must follow @all\n'
                        elif topic == '@' + user:
                            response = 'Error:  Cannot unfollow yourself\n'
                        elif client_remove_follow(user, topic):
                            response = f'No longer following {topic}\n'
                        else:
                            response = f'Error:  Was not following {topic}\n'
                        wait_for_ack(sock, addr, response, seq)
                #handle file transfer request
                elif ((len(message_parts) >= 3) and (message_parts[1] == '!attach')):
                    filename = message_parts[2]
                    message_parts.remove('!attach')
                    message_parts.remove(filename)
                    response = f'ATTACH {filename} CHAT/1.0\n'
                    wait_for_ack(sock, addr, response, seq)
                #communicatd about file information to transfer
                elif message_parts[0] == 'Content-Length:':
                    username = message_parts[5]
                    filename = message_parts[3]
                    attach_size = message_parts[1]
                    if attach_size == "-1":
                        response = f'Error:  Attached file {filename} could not be sent\n'
                    else:
                        response = f'ATTACHMENT {filename} CHAT/1.0\nOrigin: {username}\nContent-Length: {attach_size}\n'
                        file_header = response
                        readyToReceiveFile = True
                        print(f"Incoming file: {filename}")
                        print(f'Origin: {user}')
                        print(f'Content-Length: {attach_size}')
                        sel.unregister(sock)
                        sel.register(sock, selectors.EVENT_READ, handle_file_transfer)

                    wait_for_ack(sock, addr,response, seq)
                #if not specific command, simple print msg
                else:
                    print(f'Received message from user {user}:  ' + received_text)

        else:
            processed_seq = 1 - seq
            ack_packet = create_packet(processed_seq, len(ack_msg), ack_msg)
            sock.sendto(ack_packet, addr)
    else:
        pass

#return True if the received checnksum is equal to computed checksum
def check(seq,size,data,checksum):
    values = (seq, size, data)
    packer = struct.Struct(f'I I {MAX_STRING_SIZE}s')
    packed_data = packer.pack(*values)
    computed_checksum = bytes(hashlib.md5(packed_data).hexdigest(), encoding="UTF-8")
    if checksum == computed_checksum:
        return True
    else:
        return False

#accept client if received appropriate msg and if client has not registered
def accept_client(sock, mask):
    global seq
    received_packet, addr = sock.recvfrom(BUFFER_SIZE)
    unpacker = struct.Struct(f'I I {MAX_STRING_SIZE}s 32s')
    UDP_packet = unpacker.unpack(received_packet)
    received_sequence = UDP_packet[0]
    received_size = UDP_packet[1]
    received_data = UDP_packet[2]
    received_checksum = UDP_packet[3]
    if check(received_sequence,received_size,received_data,received_checksum) == True:
        ack_msg = 'ACK'.encode()
        if received_sequence == seq:
            ack_packet = create_packet(seq, len(ack_msg), ack_msg)
            sock.sendto(ack_packet, addr)
            seq = 1 - seq

            received_text = received_data[:received_size].decode()
            if received_text != 'DUMMY':
                print(f'received: {received_text}')
            message_parts = received_text.split()
            # Check format of request.
            # After sending ack to the registration message, now we want to verify if the format is correct
            if received_text == 'DUMMY':
                pass
            else:
                if ((len(message_parts) != 3) or (message_parts[0] != 'REGISTER') or (message_parts[2] != 'CHAT/1.0')):
                    print('Error:  Invalid registration message.')
                    print('Received: ' + received_text)
                    print('Connection closing ...')
                    response = '400 Invalid registration\n'
                    wait_for_ack(sock, addr, response,seq)

                else:
                    user = message_parts[1]
                    #make sure username cant be all
                    if user == 'all':
                        print('Error:  Client cannot use reserved user name \'all\'.')
                        print('Connection closing ...')
                        response = '402 Forbidden user name\n'
                        wait_for_ack(sock, addr, response, seq)

                    elif (client_search(user) == None):
                        # Check for following terms or an issue with the request.
                        # Add the user to their follow list, so @user finds them.  We'll also do @all as well for broadcast messages.
                        follow_terms = []
                        follow_terms.append(f'@{user}')
                        follow_terms.append('@all')
                        client_add(user, addr, follow_terms)
                        print(
                            f'Connection to client established, waiting to receive messages from user \'{user}\'...')
                        response = '200 Registration successful\n'
                        wait_for_ack(sock, addr, response, seq)
                        sel.unregister(server_socket)
                        sel.register(sock, selectors.EVENT_READ, read_message)
                    else:
                        #return error msg is the user has registered
                        print('Error:  Client already registered.')
                        print('Connection closing ...')
                        response = '401 Client already registered\n'
                        wait_for_ack(sock, addr, response, seq)

        else:
            processed_seq = 1 - seq
            ack_packet = create_packet(processed_seq, len(ack_msg), ack_msg)
            sock.sendto(ack_packet, addr)
    else:
        pass


def main():
    global server_socket
    global accepted
    global seq

    #initialize global seq number
    seq = 0
    # Register our signal handler for shutting down.
    signal.signal(signal.SIGINT, signal_handler)

    # Create the socket.  We will ask this to work on any interface and to pick
    # a free port at random.  We'll print this out for clients to use.
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.bind(('', 0))
    print('Will wait for client connections at port ' + str(server_socket.getsockname()[1]))
    server_socket.setblocking(True)
    sel.register(server_socket, selectors.EVENT_READ, accept_client)

    # Keep the server running forever, waiting for connections or messages.
    while (True):
        events = sel.select()
        for key, mask in events:
            callback = key.data
            callback(key.fileobj, mask)

if __name__ == '__main__':

    main()





