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

#dem variables
# redis
c = tornadoredis.Client()
c.connect()

r = redis.StrictRedis()

strims = {}
clients = {}
ping_every = 15

def strimCounts():
	strims = r.hgetall('strims') or []
	counts = {}
	for strim in strims:
		counts[strim] = strims[strim]
	return counts

def numClients():
	return r.hlen('clients')

#takes care of updating console
def printStatus():
	threading.Timer(24, printStatus).start()
	print 'Currently connected clients: ' + str(numClients())
	yield sweepClients()
	yield sweepStreams()
	strim_counts = strimCounts()
	for key, value in strim_counts.items():
		print key, value

@tornado.gen.engine
def sweepClients():
	global ping_every
	clients = yield tornado.gen.Task(c.hkeys, 'clients')
	to_remove = []
	expire_time = (time.time()-(5*ping_every))
	for client_id in clients:
		# client = clients[client_id]
		lpt = yield tornado.gen.Task(c.hget, 'last_pong_time', client_id)
		if (((lpt == '') or (lpt == None)) or (float(lpt) < expire_time)):
			# if(("last_pong_time" in client) and (client["last_pong_time"] < (t_now-(5*ping_every)))):
			to_remove.append(client_id)
	for client_id in to_remove:
		remove_viewer(client_id)

#remove the dict key if nobody is watching DaFeels
@tornado.gen.engine
def sweepStreams():
	try:
		strims = yield tornado.gen.Task(c.hgetall, 'strims')
	except Exception, e:
		print 'ERROR: Failed to get all strims from redis in order to sweep'
		print e
	else:
		if isinstance(strims, int):
			print 'got', strims, 'instead of actual strims'
			return
		to_remove = []
		for strim in strims:
			if(strims[strim] <= 0):
				to_remove.append(strim)
		num_deleted = yield tornado.gen.Task(c.hdel, 'strims', to_remove)
		print "deleted this many strims: ", str(num_deleted)
		print "should have deleted this many: ", str(len(to_remove)) 

@tornado.gen.engine
def remove_viewer(v_id):
	global c
	strim = yield tornado.gen.Task(c.hget, 'clients', v_id)
	if strim != '':
		new_count = yield tornado.gen.Task(c.hincrby, 'strims', strim, -1)
		if new_count <= 0:
			num_deleted = yield tornado.gen.Task(c.hdel, 'strims', strim)
			if num_deleted == 0:
				print "deleting this strim counter did not work : ", strim 
	else:
		print 'deleting strim-less vid:', v_id
	clients_deleted = yield tornado.gen.Task(c.hdel, 'clients', v_id)
	if clients_deleted == 0:
		print "deleting this client did not work: ", v_id
	pong_times_deleted = yield tornado.gen.Task(c.hdel, 'last_pong_time', v_id)
	if pong_times_deleted == 0:
		print "deleting this pong tracker did not work: ", v_id
	print str(numClients()) + " viewers remain connected"

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
		self.id = str(uuid.uuid4())
		print 'Opened Websocket connection: (' + self.request.remote_ip + ') ' + socket.getfqdn(self.request.remote_ip) + " id: " + self.id
		clients[self.id] = {'id': self.id}
		client_set_or_updated = yield tornado.gen.Task(self.client.hset, 'clients', self.id, '')
		if client_set_or_updated == 1:
			print client_set_or_updated, "creating new client id: ", self.id
		else:
			print client_set_or_updated, "WARN: updating old client id: ", self.id
		len_clients = yield tornado.gen.Task(self.client.hlen, 'clients')
		print 'len_clients is ', len_clients
		lpt_set_or_updated = yield tornado.gen.Task(self.client.hset, 'last_pong_time', self.id, time.time())
		if lpt_set_or_updated == 1:
			print lpt_set_or_updated, "creating last_pong_time on open with:", self.id, lpt_set_or_updated
		else:
			print lpt_set_or_updated, "WARN: updating last_pong_time on open with:", self.id, lpt_set_or_updated
		# Ping to make sure the agent is alive.
		self.io_loop.add_timeout(datetime.timedelta(seconds=(ping_every/3)), self.send_ping)
	
	@tornado.gen.engine
	def on_connection_timeout(self):
		print "-- Client timed out due to PINGs without PONGs"
		# this might be redundant and redundant
		remove_viewer(self.id)
		self.close()

	@tornado.gen.engine
	def send_ping(self):

		print("<- [PING] " + self.id)
		try:
			self.ping(self.id)
			# global ping_every
			self.ping_timeout = self.io_loop.add_timeout(datetime.timedelta(seconds=ping_every), self.on_connection_timeout)
		except Exception as ex:
			print("-- ERROR: Failed to send ping! to: "+ self.id + " because of " + repr(ex))
			self.on_connection_timeout()
		
	@tornado.gen.engine
	def on_pong(self, data):
		# We received a pong, remove the timeout so that we don't
		# kill the connection.
		print("-> [PONG] %s" % data)

		if hasattr(self, "ping_timeout"):
			self.io_loop.remove_timeout(self.ping_timeout)

		in_clients = yield tornado.gen.Task(c.hexists, 'clients', self.id)
		if in_clients:
			res = yield tornado.gen.Task(c.hset, 'last_pong_time', self.id, time.time())
			if res != True:
				print "creating last_pong_time on_pong is messed up with:", self.id, res
			# Wait some seconds before pinging again.
			global ping_every
			self.io_loop.add_timeout(datetime.timedelta(seconds=ping_every), self.send_ping)

	@tornado.gen.engine
	def on_message(self, message):
		global strims
		global numClients
		global clients
		fromClient = json.loads(message)
		action = fromClient[u'action']
		strim = fromClient[u'strim']

		if strim == "/destinychat?s=strims&stream=":
			strim = "/destinychat"

		#handle session counting - This is a fucking mess :^(
		if action == "join":
			res = yield tornado.gen.Task(self.client.hset, 'clients', self.id, strim)
			if res != True:
				print "joining strim is messed up with:", self.id, res
			strim_count = yield tornado.gen.Task(self.client.hincrby, 'strims', strim, 1)
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
			self.write_message(json.dumps({"streams":strimCounts(), "totalviewers":numClients}))

		else:
			print 'WTF: Client sent unknown command >:( %s' % (action)

	@tornado.gen.engine
	def on_close(self):
		print 'Closed Websocket connection: (' + self.request.remote_ip + ') ' + socket.getfqdn(self.request.remote_ip)+ " id: "+self.id
		global remove_viewer
		remove_viewer(self.id)

#print console updates
printStatus()

#JSON api server
class APIHandler(tornado.web.RequestHandler):
		def get(self):
				self.write(json.dumps({"streams":strimCounts(), "totalviewers":numClients()}))

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
