#!/usr/bin/env python
'''
RealSID.py
Version 1.1 - customized for espSID

Copyright (c) 2013, A.T.Brask (atbrask[at]gmail[dot]com)
All rights reserved,

Very rudimentary SID player for demonstrating the RealSIDShield Arduino shield.
Basically, it emulates a 6502 CPU with 64KB of memory. No VIC or CIA chips.
It works by running the play routine at ~50 Hz. After each run, it updates the
registers in real SID chip connected via the Arduino.

This version has been modified to use TCP and output debug information about
the SID register state.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
'''

import argparse
import Queue
from threading import Thread
import socket
from py65.devices import mpu6502
from binascii import unhexlify

XMIT_PKTS = 50
TCP_IP = '192.168.9.33'
TCP_PORT = 1337
BUFFER_SIZE = 8

def getSIDstate(memory):
    return ''.join(["{0:02X}".format(byte) for byte in memory[0xD400:0xD419]])

def runCPU(cpu, newpc, newa, newx, newy):
    cpu.pc = newpc
    cpu.a = newa
    cpu.x = newx
    cpu.y = newy
    cpu.sp = 0xFF
    running = True
    instructioncount = 0
    while running and instructioncount < 1000000:
        ## Test for return instructions RTI (0x40) and RTS (0x60)
        if cpu.ByteAt(cpu.pc) in (0x40, 0x60) and cpu.sp == 0xFF:
            running = False

        ## Test for BRK (0x00)
        if cpu.ByteAt(cpu.pc) == 0x00:
            running = False

        ## Step one instruction
        cpu.step()
        instructioncount += 1

        ## Test for jump into Kernal interrupt handler exit
        if (cpu.ByteAt(0x01) & 0x07) != 0x5 and cpu.pc in (0xea31, 0xea81):
            running = False

def send_sid(q, ip):
    ## start TCP connection
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print("Connecting to server {} on port {}".format(ip, TCP_PORT))
    s.connect((ip, TCP_PORT))

    ## Dump SID Data
    print('\n{:>5} | {:<14} | {:<14} | {:<14} | {}'.format('#', 'Voice 1', 'Voice 2', 'Voice 3', 'Filter'))
    print('-' * 70)
    idx = 0
    while True:
        try:
            for frame in iter(q.get_nowait, ''):
                s.send(unhexlify(frame))
                print('{:>5} | {} | {} | {} | {}'.format(idx, frame[0:14], frame[14:28], frame[28:42], frame[42:]))
                q.task_done()
                idx += 1
            ## Remove sentinels from the queue
            q.task_done()
            ## Wait here for a request for more data
            data = s.recv(1)
            print("got: %s" % data)

        except Queue.Empty:
            s.close()
            print("")
            break

    return

def playsid(filename, subtune, playseconds, ip):
    ## Parse file header
    data = [ord(byte) for byte in open(filename, 'rb').read()]

    filetype = ''.join([chr(ch) for ch in data[0:4]])
    version = data[5]
    print("SID type: {0} (version {1})".format(filetype, version))
    if filetype == "RSID":
        print("Warning: RSID files may not play properly. YMMV.")

    dataoffset = (data[6] << 8) | data[7]

    loadaddress = (data[8] << 8) | data[9]
    print("Load address: {0:04X}".format(loadaddress))

    initaddress = (data[10] << 8) | data[11]
    print("Init address: {0:04X}".format(initaddress))

    playaddress = (data[12] << 8) | data[13]
    print("Play address: {0:04X}".format(playaddress))

    songs = (data[14] << 8) | data[15]
    defaultsong = (data[16] << 8) | data[17]
    print("Found {0} song(s) (default song is {1})".format(songs, defaultsong))
    if subtune == -1 or subtune > songs:
        subtune = defaultsong

    speed = (data[18] << 24) | (data[19] << 16) | (data[20] << 8) | data[21]
    if speed == 0:
        print("Using 50Hz vertical blank interrupt.")
    else:
        print("Warning: Some songs require the CIA 1 timer (not implemented).")

    title = ''.join([chr(ch) for ch in data[22:54]])
    author = ''.join([chr(ch) for ch in data[54:86]])
    released = ''.join([chr(ch) for ch in data[86:118]])

    print("Title    : {0}".format(title))
    print("Author   : {0}".format(author))
    print("released : {0}".format(released))

    ## Check load address
    if loadaddress == 0:
        print("Warning: SID has load address 0, reading from C64 binary data")
        loadaddress = data[dataoffset] | (data[dataoffset + 1] << 8)
        dataoffset += 2
        print("New load address is {0:04X}".format(loadaddress))

    ## Check init address
    if initaddress == 0:
        print("Warning: SID has init address 0, cloning load address instead")
        initaddress = loadaddress
        print("New init address is {0:04X}".format(initaddress))

    ## Setup memory
    memory = [0] * 0x10000
    memory[0x01] = 0x37;
    for idx, byte in enumerate(data[dataoffset:]):
        memory[loadaddress + idx] = byte

    ## Setup CPU
    cpu = mpu6502.MPU(memory)

    ## Init SID tune
    print("Initializing song {0}...".format(subtune))
    runCPU(cpu, initaddress, subtune - 1, 0, 0)

    ## Check play address
    if playaddress == 0:
        print("Warning: SID has play address 0, reading from interrupt vector")
        if (memory[0x01] & 0x07) == 0x5:
            playaddress = memory[0xfffe] | (memory[0xffff] << 8)
        else:
            playaddress = memory[0x314] | (memory[0x315] << 8)
        print("New play address is {0:04X}".format(playaddress))

    ## Play SID tune!
    if playseconds == -1:
        print("Playing...")
    else:
        print("Playing for {0} seconds...".format(playseconds))

    frame_num = 0
    total_frames = playseconds * 50
    frames = Queue.Queue()
    while(playseconds == -1 or frame_num < total_frames):
        runCPU(cpu, playaddress, 0, 0, 0)
        frames.put(getSIDstate(memory))
        frame_num += 1
        if (frame_num % XMIT_PKTS == 0):
            frames.put('')
            ## Start transmission when XMIT_PKTS is reached
        if (frame_num == XMIT_PKTS) or (total_frames < XMIT_PKTS and frame_num == total_frames):
            worker = Thread(target=send_sid, args=(frames,ip,))
            worker.setDaemon(True)
            worker.start()
    else:
        frames.put('')
        ## Block until queue is exhausted.
        frames.join()
        print("Done...")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RealSID.py v1.0")
    parser.add_argument("filename", help="Input SID file")
    parser.add_argument("-s", "--song", type=int, default=-1, help="The song number to be played (default is specified in the SID file)")
    parser.add_argument("-t", "--time", type=int, default=-1, help="The desired playtime in seconds (default is forever)")
    parser.add_argument("-i", "--ip", type=str, default=TCP_IP, help="The IP to send SID data to.")
    args = parser.parse_args()
    
    queue = playsid(args.filename, args.song, args.time, args.ip)


