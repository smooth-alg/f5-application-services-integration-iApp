#!/usr/bin/python

import time
import json
import sys
import os
import argparse
import requests

try: 
	requests.packages.urllib3.disable_warnings()
except:
	pass

class AppSvcsBigiq:

	options = {
		'debug': False,
		'bigiphost':'',
		'bigipusername':'',
		'bigippassword':'',
		'bigiprootpassword':'',
		'bigiqhost':'',
		'bigiqusername':'',
		'bigiqpassword':''
	}

	def __init__(self, **kwargs):
		self.options.update(**kwargs)
		self._debug(self.options)
		self.s = requests.session()	
		self.s.auth = (self.options["bigiqusername"], self.options["bigiqpassword"])
		self.s.verify = False
		self.base_url = "https://" + self.options["bigiqhost"] + "/mgmt"
		self._check_credentials(self.options["bigiqhost"])

	def _debug(self, msg):
		if self.options["debug"]:
			sys.stderr.write("[debug] %s: %s\n" % (sys._getframe(1).f_code.co_name, msg))
	
	def _error(self, msg, code=1):
		sys.stderr.write("[error] %s\n" % msg)
		sys.exit(code)

	def _check_credentials(self, host):
		resp = self.s.get(self.base_url + "/shared/echo")
		self._debug("resp.status_code=%s" % resp.status_code)
		if resp.status_code == 401:
			self._error("Authentication to %s failed" % host)

		respjson = json.loads(resp.text)
		self._debug("respjson=%s" % respjson)
		if respjson["stage"] != "STARTED":
			self._error("The BIG-IQ host at %s does not appear to be available yet" % host)

		return 1
	
	def _check_response(self, response, response_code=requests.codes.ok):
		if response.status_code != response_code:
			self._error("REST request failed [%s]: %s" % (response.status_code, response.text))

		return 1

	def _poll_until(self, url, key, val, count=60, wait=1):
		self._debug("url=%s key=%s val=%s count=%s wait=%s" % (url, key, val, count, wait))
		for x in range(0, count):
			resp = self.s.get(url)
			self._check_response(resp)
			respjson = json.loads(resp.text)
			self._debug(" [%s] val=%s %s=%s" % (x, val, key, respjson[key]))
			if str(respjson[key]) == str(val):
				return respjson
			time.sleep(wait)

		return {}

	def _is_bigip_in_device_trust(self):
		resp = self.s.get(self.base_url + "/cm/global/tasks/device-trust")
		self._check_response(resp)
		respjson = json.loads(resp.text)
		self._debug("respjson=%s" % respjson)		
		if 'items' in respjson.keys():
			for i in respjson["items"]:
				if i["address"] == self.options["bigiphost"] and i["status"] == "FINISHED":
					self._debug("found BIG-IP: %s" % i)
					self._trust_id = i["id"]
					self._trust_idUrl = "/cm/global/tasks/device-trust/" + self._trust_id
					self._trust_machineId = i["machineId"]
					self._trust_machineIdUrl = "/cm/global/tasks/device-trust/" + self._trust_machineId
					self._resolver_machineId = "cm/system/machineid-resolver/" + self._trust_machineId
					return 1

		self._debug("did not find BIG-IP")
		return 0

	def _add_bigip_to_device_trust(self):
		if self._is_bigip_in_device_trust():
			return 1

		postdata = {
			"address":self.options["bigiphost"],
			"userName":self.options["bigipusername"],
			"password":self.options["bigippassword"],
			"clusterName":"",
			"useBigiqSync":"false"
		}
		self._debug("postdata=%s" % postdata)
		resp = self.s.post(self.base_url + "/cm/global/tasks/device-trust", data=json.dumps(postdata))
		self._check_response(resp, 202)
		respjson = json.loads(resp.text)
		self._debug("respjson=%s" % respjson)
		self._trust_id = respjson["id"]
		self._trust_idUrl = "/cm/global/tasks/device-trust/" + self._trust_id
		trustjson = self._poll_until(self.base_url + self._trust_idUrl, "status", "FINISHED")
		if not bool(trustjson):
			self._error("BIG-IP device could not be added to BIG-IQ device trust: %s" % trustjson)

		self._trust_machineId = trustjson["machineId"]
		self._trust_machineIdUrl = "/cm/global/tasks/device-trust/" + self._trust_machineId
		self._resolver_machineId = "cm/system/machineid-resolver/" + self._trust_machineId
		self._debug("machineId=%s" % self._trust_machineId)
		self._debug("resolver_machineId=%s" % self._resolver_machineId)

	def _is_bigip_discovered(self):
		if not self._is_bigip_in_device_trust():
			return 0

		resp = self.s.get(self.base_url + "/cm/global/tasks/device-discovery")
		self._check_response(resp)
		respjson = json.loads(resp.text)
		self._debug("respjson=%s" % respjson)		
		if 'items' in respjson.keys():
			for i in respjson["items"]:
				if i["deviceReference"]["link"] == self._resolver_machineId and i["status"] == "FINISHED":
					self._debug("found BIG-IP: %s" % i)
					self._discovery_id = i["id"]
					self._discovery_idUrl = "/cm/global/tasks/device-discovery/" + self._discovery_id
					return 1

		self._debug("did not find BIG-IP")
		return 0

	def _discover_bigip_device(self):
		postdata = {
			"deviceReference":{"link":self._resolver_machineId},
			"moduleList": [
				{"module": "adc_core"}, 
				{"module": "asm"},
				{"module": "security_shared"}
			], 
			"userName": self.options["bigipusername"], 
			"password": self.options["bigippassword"], 
			"rootUser": "root", 
			"rootPassword": self.options["bigiprootpassword"], 
			"automaticallyUpdateFramework": "true"
		}
		resp = self.s.post(self.base_url + "/cm/global/tasks/device-discovery", data=json.dumps(postdata))
		self._check_response(resp, 202)
		respjson = json.loads(resp.text)
		self._debug("respjson=%s" % respjson)
		self._discovery_id = respjson["id"]
		self._discovery_idUrl = "/cm/global/tasks/device-discovery/" + self._discovery_id
		discoveryjson = self._poll_until(self.base_url + self._discovery_idUrl, "status", "FINISHED", 120)
		if not bool(discoveryjson):
			self._error("BIG-IP could not be discovered by BIG-IQ device: %s" % discoveryjson)

		self._debug("discoveryjson=%s" % discoveryjson)

	def _import_bigip_asm_config(self):
		postdata = {
			"deviceReference":{"link":self._resolver_machineId},
			"uuid":self._trust_machineId,
			"deviceUri":"https://" + self.options["bigiphost"],
			"machineId":self._trust_machineId
		}
		resp = self.s.post(self.base_url + "/cm/asm/tasks/declare-mgmt-authority", data=json.dumps(postdata))
		self._check_response(resp, 202)
		respjson = json.loads(resp.text)
		self._debug("respjson=%s" % respjson)
		self._importasm_id = respjson["id"]
		self._importasm_idUrl = "/cm/asm/tasks/declare-mgmt-authority/" + self._importasm_id
		importjson = self._poll_until(self.base_url + self._importasm_idUrl, "status", "FINISHED", 120)
		if not bool(importjson):
			self._error("BIG-IP ASM config could not be imported by BIG-IQ device: %s" % importjson)

		self._debug("importjson=%s" % importjson)

	def workflow_manage_asm_policies(self, **kwargs):
		self._debug("in workflow_manage_asm_policy")
		self._add_bigip_to_device_trust()
		self._discover_bigip_device()
		self._import_bigip_asm_config()
	
def main():
	parser = argparse.ArgumentParser(description="Helper script for AppSvcs iApp BIG-IQ integration")
	parser.add_argument("-D","--debug", default=False, action="store_true", help="Enable debug output")
	parser.add_argument("-ih","--bigiphost",default="10.1.1.1",help="The BIG-IP host")
	parser.add_argument("-iu","--bigipusername",default="admin",help="The BIG-IP username")
	parser.add_argument("-ip","--bigippassword",default="admin",help="The BIG-IP password")
	parser.add_argument("-ir","--bigiprootpassword",default="default",help="The BIG-IP password")
	parser.add_argument("-qh","--bigiqhost",default="10.1.1.8",help="The BIG-IQ host")
	parser.add_argument("-qu","--bigiqusername",default="admin",help="The BIG-IQ username")
	parser.add_argument("-qp","--bigiqpassword",default="admin",help="The BIG-IQ password")
	parser.add_argument("workflow",help="The workflow to execute")
	parser.add_argument("args",nargs='?', default={}, help="Arguments to the workflow in JSON format")

	args = parser.parse_args()

	iq = AppSvcsBigiq(**vars(args))

	func_name = 'workflow_%s' % args.workflow
	if hasattr(AppSvcsBigiq, func_name):
		wf_args = {}
		if bool(args.args):
			wf_args = json.loads(args.args)

		return getattr(AppSvcsBigiq, func_name)(iq, **wf_args)
	else:
		sys.stderr.write("[error] the workflow '%s' is not defined\n" % args.workflow)
		return 1


if __name__ == "__main__":
	sys.exit(main())


