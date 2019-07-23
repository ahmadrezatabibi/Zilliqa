#!/usr/bin/env python3
# Copyright (C) 2019 Zilliqa
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import mmap
import time
import re
import os
import shutil, sys
import subprocess
import tarfile
import json
import datetime
from pathlib import Path
import socket
import argparse
from urllib import request, parse

PERSISTENCE_SNAPSHOT_NAME='incremental'
STATEDELTA_DIFF_NAME='statedelta'
BUCKET_NAME='BUCKET_NAME'
NUM_TXBLOCK = 1
NUM_DSBLOCK= "PUT_INCRDB_DSNUMS_WITH_STATEDELTAS_HERE"
NUM_FINAL_BLOCK_PER_POW= "PUT_NUM_FINAL_BLOCK_PER_POW_HERE"
SOURCE = './'
TESTNET_NAME= "TEST_NET_NAME"
SYNC_INTERVAL = 1

# logger class
class Tee(object):
	def __init__(self, *files):
		self.files = files
	def write(self, obj):
		for f in self.files:
			f.write(obj)
			f.flush() # If you want the output to be visible immediately
	def flush(self) :
		for f in self.files:
			f.flush()

f = open('uploadIncrDB-log.txt', 'w')
original = sys.stdout
sys.stdout = Tee(sys.stdout, f)


def getBucketString(subFolder):
	return "s3://"+BUCKET_NAME+"/"+subFolder+"/"+TESTNET_NAME

def CreateTempPersistence():
	bashCommand = "rsync --recursive --delete -a persistence temp"
	process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE)
	output, error = process.communicate()
	print("Copied local persistence to temporary")

def CleanS3StateDeltas():
	bashCommand = "aws s3 rm --recursive "+getBucketString(STATEDELTA_DIFF_NAME)
	process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE)
	output, error = process.communicate()
	print("Cleaned S3 bucket "+getBucketString(STATEDELTA_DIFF_NAME))

def CleanS3EntirePersistence():
	bashCommand = "aws s3 rm --recursive "+ getBucketString(PERSISTENCE_SNAPSHOT_NAME)
	process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE)
	output, error = process.communicate()
	print("Cleaned S3 bucket "+getBucketString(PERSISTENCE_SNAPSHOT_NAME))

def SetLock():
	Path(".lock").touch()
	bashCommand = "aws s3 cp .lock "+getBucketString(PERSISTENCE_SNAPSHOT_NAME)+"/.lock"
	process = subprocess.Popen(bashCommand, universal_newlines=True, shell=True,stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
	output, error = process.communicate()
	print("[" + str(datetime.datetime.now()) + "] SetLock for uploading process")

def ResetLock():
	bashCommand = "aws s3 rm "+getBucketString(PERSISTENCE_SNAPSHOT_NAME)+"/.lock"
	process = subprocess.Popen(bashCommand, universal_newlines=True, shell=True,stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
	output, error = process.communicate()
	print("[" + str(datetime.datetime.now()) + "] Removed lock for uploading process")
	os.remove(".lock")

def SyncLocalToS3Persistence(blockNum,lastBlockNum):
	
	# Try uploading stateDelta diff to S3
	result = GetAndUploadStateDeltaDiff(blockNum,lastBlockNum)

	# Try syncing S3 with latest persistence only if NUM_DSBLOCK blocks have crossed.
	if ((blockNum + 1) % (NUM_DSBLOCK * NUM_FINAL_BLOCK_PER_POW) == 0 or lastBlockNum == 0):
		bashCommand = "aws s3 sync --delete temp/persistence "+ getBucketString(PERSISTENCE_SNAPSHOT_NAME)+"/persistence --exclude 'diagnosticNodes/*' --exclude 'diagnosticCoinb/*' "
		process = subprocess.Popen(bashCommand, universal_newlines=True, shell=True,stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
		output, error = process.communicate()
		if re.match(r'^\s*$', output):
			print("No entire persistence diff, interesting...")
		else:
			print("Remote S3 bucket: "+getBucketString(PERSISTENCE_SNAPSHOT_NAME)+"/persistence is entirely Synced")
		# clear the state-delta bucket now.
		if(lastBlockNum != 0):
			CleanS3StateDeltas()

	elif (result == 0): # if has state delta diff, we still need to sync persistence/stateDelta so that next time for next blocknum we can get statedelta diff correctly
		bashCommand = "aws s3 sync --delete temp/persistence "+getBucketString(PERSISTENCE_SNAPSHOT_NAME)+"/persistence --exclude '*' --include 'microBlocks/*' --include 'dsBlocks/*' --include 'dsCommittee/*' --include 'shardStructure/*' --include 'txBlocks/*' --include 'VCBlocks/*' --include 'blockLinks/*' --include 'fallbackBlocks/*' --include 'metaData/*' --include 'stateDelta/*' --include 'txBodies/*' "
		process = subprocess.Popen(bashCommand, universal_newlines=True, shell=True,stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
		output, error = process.communicate()
		print("Remote S3 bucket: "+getBucketString(PERSISTENCE_SNAPSHOT_NAME)+"/persistence is Synced without state/stateRoot/contractCode/contractStateData/contractStateIndex")
	else:
		print("Not supposed to upload state now!")

def path_leaf(path):
    head, tail = os.path.split(path)
    return tail or os.path.basename(head)

def GetAndUploadStateDeltaDiff(blockNum, lastBlockNum):
	global start
	# check if there is diff and buffer the diff_output
	bashCommand = "aws s3 sync --dryrun --delete temp/persistence/stateDelta "+ getBucketString(PERSISTENCE_SNAPSHOT_NAME)+"/persistence/stateDelta"
	process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE)
	diff_output, error = process.communicate()
	str_diff_output = diff_output.decode("utf-8")
	if re.match(r'^\s*$', str_diff_output):
		print("No state delta diff, interesting...")
		tf = tarfile.open("stateDelta_"+str(blockNum)+".tar.gz", mode="w:gz")
		t = tarfile.TarInfo("stateDelta_"+str(blockNum))
		t.type = tarfile.DIRTYPE
		tf.addfile(t)
		tf.close()
		bashCommand = "aws s3 cp stateDelta_"+str(blockNum)+".tar.gz "+getBucketString(STATEDELTA_DIFF_NAME)+"/stateDelta_"+str(blockNum)+".tar.gz"
		process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE)
		output, error = process.communicate()
		print("DUMMY upload: State-delta Diff for new txBlk :" + str(blockNum) + ") in Remote S3 bucket: "+ getBucketString(STATEDELTA_DIFF_NAME)+" is Synced")
		os.remove("stateDelta_"+str(blockNum)+".tar.gz")
		start = (int)(time.time()) # reset inactive start time - delta was uploaded
		return 1

	if(blockNum % NUM_FINAL_BLOCK_PER_POW == 0 or (lastBlockNum == 0)):
		# we dont need to upload diff here. Instead complete stateDelta
		tf = tarfile.open("stateDelta_"+str(blockNum)+".tar.gz", mode="w:gz")
		tf.add("temp/persistence/stateDelta", arcname=os.path.basename("persistence/stateDelta_"+str(blockNum)))
		tf.close()
		bashCommand = "aws s3 cp stateDelta_"+str(blockNum)+".tar.gz "+getBucketString(STATEDELTA_DIFF_NAME)+"/stateDelta_"+str(blockNum)+".tar.gz"
		process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE)
		output, error = process.communicate()
		print("New state-delta snapshot for new ds epoch (TXBLK:" + str(blockNum) + ") in Remote S3 bucket: "+getBucketString(STATEDELTA_DIFF_NAME)+" is Synced")
		os.remove("stateDelta_"+str(blockNum)+".tar.gz")
		start = (int)(time.time()) # reset inactive start time - delta was uploaded
		return 0

	str_diff_output = str_diff_output.strip()
	splitted = str_diff_output.split('\n')
	result=[]
	if(len(splitted) > 0):
		for x in splitted:
			tok = x.split(' ');
			# skip deleted files
			if(len(tok) >= 3 and tok[1] == "upload:"): 
				result.append(tok[2])

		tf = tarfile.open("stateDelta_"+str(blockNum)+".tar.gz", mode="w:gz")
		for x in result:
			tf.add(x,arcname="stateDelta_"+str(blockNum)+"/"+ path_leaf(x))
		tf.close()
		bashCommand = "aws s3 cp stateDelta_"+str(blockNum)+".tar.gz "+getBucketString(STATEDELTA_DIFF_NAME)+"/stateDelta_"+str(blockNum)+".tar.gz"
		process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE)
		output, error = process.communicate()
		print("State-delta Diff for new txBlk :" + str(blockNum) + ") in Remote S3 bucket: "+getBucketString(STATEDELTA_DIFF_NAME)+" is Synced")
		os.remove("stateDelta_"+str(blockNum)+".tar.gz")
		start = (int)(time.time()) # reset inactive start time - delta was uploaded
		return 0 #success
	return 1

def CleanupDir(folderName):
        if os.path.exists("./"+folderName):
                shutil.rmtree(folderName)

def send_packet_tcp(data, host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
        data=data+'\n'
        data=data.encode()
        sock.sendall(data)
        received = recvall(sock)
    except socket.error:
        print("Socket error")
        sock.close()
        return None
    sock.close()
    return received

def recvall(sock):
        BUFF_SIZE = 4096 # 4 KiB
        data = ""
        while True:
                part = str(sock.recv(BUFF_SIZE),'utf-8')
                data += part
                if len(part) < BUFF_SIZE:
                        # either 0 or end of data
                        break
        return data

def get_response(params, methodName, host, port):

    data = json.dumps(generate_payload(params, methodName))
    recv = send_packet_tcp(data, host, port)
    if not recv:
        return None
    response = json.loads(recv)

    return response

def generate_payload(params, methodName, id = 1):
    if params and not type(params) is list:
        params = [params]
    payload = {"method":methodName,
                "params":params,
                "jsonrpc":"2.0",
                "id" : id
    }
    return payload

def GetCurrentTxBlockNum():
	loaded_json = get_response([], 'GetEpochFin', '127.0.0.1', 4301)
	blockNum = -1
	if loaded_json == None:
		return blockNum
	val = loaded_json["result"]
	if (val != None and val != ''):
		blockNum = int(val) - 1 # -1 because we need TxBlockNum (not epochnum)
	return blockNum

def send_report(msg, url):
        post = {'text': '```' + msg + '```'}
        json_data = json.dumps(post)
        req = request.Request(url, data=json_data.encode('ascii'))
        resp = request.urlopen(req)

def SendAlertIfInactive(blockNum):
	global start
	global webhook
	global tt, dd
	
	inactiveSeconds = 5 * tt
	
	if blockNum == -1:
		errmsg = "Alert - Failed to receive TxBlk info from lookup, please investigate! "
		print ("[" + str(datetime.datetime.now()) + "] " + errmsg)
		if  webhook != '':
			send_report(errmsg, webhook)
	elif (blockNum + 1) % NUM_FINAL_BLOCK_PER_POW == 0:
		inactiveSeconds = dd + inactiveSeconds

	end = (int)(time.time())
	if (end - start) > inactiveSeconds:
		if SendAlertIfInactive.lastBlockNum  == blockNum:
			SendAlertIfInactive.counter += 1
		else:
			SendAlertIfInactive.counter = 1
			#slack alert
		try:
			errmsg = "Alert -No activity on upload to S3 since txBlk:" + str(blockNum) + " for more than " + str((inactiveSeconds*SendAlertIfInactive.counter/60))+ " mins!\n \
Please investigate unless viewchange happened!"
			print ("[" + str(datetime.datetime.now()) + "] " + errmsg)
			if webhook != '':
				send_report(errmsg, webhook)
		except Exception as e:
			print(e)
			pass
		start = (int)(time.time())
	elif SendAlertIfInactive.lastBlockNum  != blockNum:
		SendAlertIfInactive.counter = 0
	SendAlertIfInactive.lastBlockNum = blockNum

#initialize function's static variable
SendAlertIfInactive.counter = 0
SendAlertIfInactive.lastBlockNum = 0

def shallStart():
	result =  False, -1
	curr_blockNum = GetCurrentTxBlockNum()
	if (curr_blockNum == -1):
		SendAlertIfInactive(curr_blockNum)
		return False, -1

	# next expected txBlock to start this script
	if curr_blockNum < NUM_FINAL_BLOCK_PER_POW :
		# This will avoid starting in first DS epoch
		next_blockNum = NUM_FINAL_BLOCK_PER_POW
	elif ((curr_blockNum + 2) % NUM_FINAL_BLOCK_PER_POW) == 0:
		# This will avoid starting in vacaous epoch
		next_blockNum = curr_blockNum + 2
	else:
		next_blockNum = curr_blockNum + 1

	# wait for next txBlock to be mined
	last_blockNum = curr_blockNum
	while True:
		time.sleep(1)
		blockNum = GetCurrentTxBlockNum()
		if (blockNum == -1):
			# No txblock is received. check for inactvity
			SendAlertIfInactive(blockNum)
			return False, blockNum
		elif (blockNum > next_blockNum):
			start = (int)(time.time())
			return False, blockNum
		elif (blockNum == next_blockNum):
			return True, blockNum
		elif (blockNum > last_blockNum):
			start = (int)(time.time())
			last_blockNum = blockNum
		else: # No next txblock received.check for inactivity
			SendAlertIfInactive(blockNum)

def main():
	isVacaous = False
	lastBlockNum = 0
	# clear the entire incremental bucket now.
	CleanS3EntirePersistence()
	# clear the state-delta bucket now.
	CleanS3StateDeltas()
	shallStartFlag = False
	blockNum = -1
	global start
	start = (int)(time.time()) # script started. set `start` time being inactive
	while True:
		try:
			if shallStartFlag == False:
				shallStartFlag, blockNum = shallStart()
				if shallStartFlag == False:
					SendAlertIfInactive(blockNum) #Not started uploading to S3 for long
					time.sleep(1)
					continue
				start = (int)(time.time()) # reset inactive start time since shall start is signaled
			else:
				blockNum = GetCurrentTxBlockNum()
				if (blockNum == -1):
					SendAlertIfInactive(blockNum)
					time.sleep(1)
					continue

			if ( (lastBlockNum == 0 and blockNum > -1) or (blockNum >= lastBlockNum + NUM_TXBLOCK)):
				# try syncing every N txn blks or if its vacaous epoch
					print("TxBlk: " + str(blockNum))
					SetLock()
					# create temp copy of local persistence
					CreateTempPersistence()
					# upload/sync the temporary copied persistence with S3
					SyncLocalToS3Persistence(blockNum,lastBlockNum)
					lastBlockNum = blockNum
					ResetLock()
			
			SendAlertIfInactive(blockNum) # Not uploaded state-delta for long
			time.sleep(SYNC_INTERVAL)
		except Exception as e:
			print(e)
			time.sleep(SYNC_INTERVAL)
			pass

if __name__ == '__main__':
	print("Starting upload to S3 process...")

	parser = argparse.ArgumentParser(description='upload incremental script')
	parser.add_argument('-w','--webhook', help='Slack webhook URL', required=False, default='')
	parser.add_argument('-t','--txblktime', help='Avg txBlockTime to get mined', required=False, default=60)
	parser.add_argument('-d','--dsblktime', help='Avg dsBlockTime to get mined', required=False, default=600)
	args = vars(parser.parse_args())

	global webhook
	global tt, dd
	tt = int(args['txblktime'])
	dd = int(args['dsblktime'])
	webhook = args['webhook']

	# create temp folder
	if not os.path.exists(SOURCE+'temp'):
		os.makedirs(SOURCE+'temp')
	CleanupDir(SOURCE+'temp')
	os.chdir(SOURCE)

	main()
	f.close()
