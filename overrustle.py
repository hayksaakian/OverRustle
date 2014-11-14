import threading
import tornado.httpserver
import tornado.websocket
import tornado.ioloop
import tornado.web
import tornadoredis
import tornado.gen
import redis
import json
import socket
import time
import datetime
import random
import uuid
import re
from collections import OrderedDict 

#dem variables
# redis
c = tornadoredis.Client()
c.connect()

r = redis.StrictRedis()

strims = {}
clients = {}
ping_every = 15

def apiDump():
	counts = strimCounts()
	totalviewers = 0
	idleclients = counts.get("connecting", 0)
	for strim in counts:
		totalviewers = totalviewers + counts[strim]
	sorted_counts = OrderedDict(sorted(counts.items(), key=lambda t: t[1], reverse=True))
	totalviewers = totalviewers - idleclients
	# remove 'connecting' from counts
	sorted_counts.pop("connecting", None)
	return {"streams":sorted_counts, "totalviewers":totalviewers,"totalclients":numClients(),"idleclients":idleclients}

def strimCounts():
	def num(s):
		try:
			return int(s)
		except ValueError:
			return float(s)
	strims = r.hgetall('strims') or []
	counts = {}
	for strim in strims:
		counts[strim] = num(strims[strim])
	return counts

def numClients():
	return r.hlen('clients')

#takes care of updating console
def printStatus():
	trd = threading.Timer(8, printStatus)
	trd.daemon = True # lets us use Ctrl+C to kill python's threads
	trd.start()
	print str(numClients()), 'clients connected right now'
	sweepClients()
	strim_counts = strimCounts()
	# print len(strim_counts), "unique strims"
	for key, value in strim_counts.items():
		print key, value

def sweepClients():
	global ping_every
	clients = r.hgetall('clients')
	if isinstance(clients, int):
		print 'got', clients, 'instead of actual clients'
		return
	to_remove = []
	expire_time = (time.time()-(2*ping_every))
	strims = {}
	all_strims = []
	for client_id in clients:
		# client = clients[client_id]
		strim = clients[client_id]
		if strim not in all_strims:
			all_strims.append(strim)
		lpt = r.hget('last_pong_time', client_id)
		# print lpt, expire_time
		if (((lpt == '') or (lpt == None)) or (float(lpt) < expire_time)):
			# if(("last_pong_time" in client) and (client["last_pong_time"] < (t_now-(5*ping_every)))):
			to_remove.append(client_id)
		else:
			# build the streams list, to sync with the actual viewer counts
			strims[strim] = strims.get(strim, 0) + 1
	# using a pipeline to ensure that the api always has something for strims
	pipe = r.pipeline()
	if len(to_remove) > 0:
		pipe.hdel('last_pong_time', *to_remove)
		pipe.hdel('clients', *to_remove)
	for strim in all_strims:
		if strim in strims:
			pipe.hset('strims', strim, strims[strim])
		else:
			pipe.hdel('strims', strim)
	responses = pipe.execute()
	# print responses
	if len(to_remove) > 0:
		lpts_removed = responses[0]
		clients_removed = responses[1]
		print 'done removing all', lpts_removed, 'out of', len(to_remove), 'last_pong_time entries', clients_removed, 'out of', len(to_remove), 'clients', to_remove

def isBad(s):
	return True in [
		re.search("[^A-z 0-9 \?\&\/=/:/-]", s),
		len(s) > 128
	]

import re

def slugify(value):
    """
    Normalizes string, converts to lowercase, removes non-alpha characters,
    and converts spaces to hyphens.
    """
    value = re.sub('[^\w\s-]', '', value).strip().lower()
    return re.sub('[-\s]+', '-', value)

#ayy lmao
#if self.is_enlightened_by(self.intelligence):
#	self.is_euphoric = True

#Stat tracking websocket server
#Hiring PHP developers does not contribute to the quota of employees with disabilities.
class WSHandler(tornado.websocket.WebSocketHandler):

	def __init__(self, application, request, **kwargs):
		tornado.websocket.WebSocketHandler.__init__(self, application, request, **kwargs)
		self.client = tornadoredis.Client()
		self.client.connect()
		self.io_loop = tornado.ioloop.IOLoop.instance()

	def check_origin(self, origin):
		return True

	@tornado.gen.engine
	def open(self):
		global clients
		self.id = str(self.request.remote_ip)+"~"+str(uuid.uuid4())

		print 'Opened Websocket connection: (' + self.request.remote_ip + ') ' + socket.getfqdn(self.request.remote_ip) + " id: " + self.id
		clients[self.id] = {'id': self.id}
		lpt_set_or_updated = yield tornado.gen.Task(self.client.hset, 'last_pong_time', self.id, time.time())
		if lpt_set_or_updated == 1:
			print "redis:", lpt_set_or_updated, "creating last_pong_time on open with:", self.id
		else:
			print "redis:", lpt_set_or_updated, "WARN: updating last_pong_time on open with:", self.id, lpt_set_or_updated
		client_set_or_updated = yield tornado.gen.Task(self.client.hset, 'clients', self.id, 'connecting')
		if client_set_or_updated == 1:
			print "redis:", client_set_or_updated, "creating new client id: ", self.id
		else:
			print "redis:", client_set_or_updated, "WARN: updating old client id: ", self.id
		try:
			len_clients = yield tornado.gen.Task(self.client.hlen, 'clients')
			print 'len_clients is', len_clients
		except Exception, e:
			print "ERROR: (with redis) calling HLEN on \'clients\'"
			print e
		# Ping to make sure the agent is alive.
		self.io_loop.add_timeout(datetime.timedelta(seconds=(ping_every/3)), self.send_ping)
	
	@tornado.gen.engine
	def on_connection_timeout(self):
		print "-- Client timed out due to PINGs without PONGs"
		# this might be redundant and redundant
		# don't ping them pointlessly
		self.remove_viewer()
		self.close()

	@tornado.gen.engine
	def send_ping(self):
		in_clients = yield tornado.gen.Task(c.hexists, 'clients', self.id)
		if in_clients:
			print "<- [PING]", self.id
			try:
				self.ping(self.id)
				# global ping_every
				self.ping_timeout = self.io_loop.add_timeout(datetime.timedelta(seconds=ping_every), self.on_connection_timeout)
			except Exception as ex:
				print "-- ERROR: Failed to send ping!", "to: "+ self.id + " because of " + repr(ex)
				self.on_connection_timeout()
		
	@tornado.gen.engine
	def on_pong(self, data):
		# We received a pong, remove the timeout so that we don't
		# kill the connection.
		print "-> [PONG]", data

		if hasattr(self, "ping_timeout"):
			self.io_loop.remove_timeout(self.ping_timeout)

		in_clients = yield tornado.gen.Task(c.hexists, 'clients', self.id)
		if in_clients:
			created_or_updated = yield tornado.gen.Task(c.hset, 'last_pong_time', self.id, time.time())
			if created_or_updated != 0:
				# 0 means the field was updated
				print "redis:", created_or_updated, "creating last_pong_time on_pong is messed up with:", self.id
			# Wait some seconds before pinging again.
			global ping_every
			self.io_loop.add_timeout(datetime.timedelta(seconds=ping_every), self.send_ping)

	@tornado.gen.engine
	def on_message(self, message):
		global clients
		fromClient = json.loads(message)
		action = fromClient[u'action']
		strim = fromClient[u'strim']

		if strim == "/destinychat?s=strims&stream=":
			strim = "/destinychat"

		if isBad(strim):
			action = ""

		#handle session counting - This is a fucking mess :^(
		if action == "join":
			created_or_updated_client = yield tornado.gen.Task(self.client.hset, 'clients', self.id, strim)
			if created_or_updated_client not in [0, 1]:
				# 1 means the already existing key was updated, 0 means it was created
				print "joining strim is messed up with:", self.id, created_or_updated_client
			try:
				strim_count = yield tornado.gen.Task(self.client.hget, 'strims', strim)
			except Exception, e:
				print "ERROR: (with " +strim+ ") failed to get the viewer count for this strim"
				print e
			else:
				self.write_message(str(strim_count) + " OverRustle.com Viewers")
				print 'User Connected: Watching %s' % (strim)

		elif action == "viewerCount":
			try:
				strim_count = yield tornado.gen.Task(self.client.hget, 'strims', strim)
			except Exception, e:
				print "ERROR: (with " +strim+ ") failed to get the viewer count for this strim"
				print e
			else:
				self.write_message(str(strim_count) + " OverRustle.com Viewers")

		elif action == "api":
			self.write_message(json.dumps(apiDump()))

		else:
			print 'WTF: Client sent unknown command >:( %s' % (action)

	@tornado.gen.engine
	def on_close(self):
		print 'Closed Websocket connection: (' + self.request.remote_ip + ') ' + socket.getfqdn(self.request.remote_ip)+ " id: "+self.id
				# don't ping them pointlessly
		self.remove_viewer()

	@tornado.gen.engine
	def remove_viewer(self):
		if hasattr(self, "ping_timeout"):
			self.io_loop.remove_timeout(self.ping_timeout)
		global c
		v_id = self.id
		in_clients = yield tornado.gen.Task(c.hexists, 'clients', v_id)
		if in_clients:
			clients_deleted = yield tornado.gen.Task(c.hdel, 'clients', v_id)
			if clients_deleted == 0:
				print "redis:", clients_deleted, v_id, "deleting this client was redundant"
		pong_times_deleted = yield tornado.gen.Task(c.hdel, 'last_pong_time', v_id)
		if pong_times_deleted == 0:
			print "redis:", pong_times_deleted, v_id, "deleting this pong tracker was redundant"
		print str(numClients()) + " viewers remain connected"

#print console updates
printStatus()

#JSON api server
class APIHandler(tornado.web.RequestHandler):
		def get(self):
				self.write(json.dumps(apiDump()))

#GET address handlers
application = tornado.web.Application([
		(r'/ws', WSHandler),
		(r'/api', APIHandler)
])
 
#starts the server on port 9998
if __name__ == "__main__":
	http_server = tornado.httpserver.HTTPServer(application)
	http_server.listen(9998)
	tornado.ioloop.IOLoop.instance().start()
