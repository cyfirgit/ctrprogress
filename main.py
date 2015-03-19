#!/usr/bin/env python
#
# Copyright 2007 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import webapp2,sys,urllib2,json
import os.path
import ranker

from lxml import html
from google.appengine.ext import ndb
from google.appengine.api import urlfetch

class InitDBHandler(webapp2.RequestHandler):
    def get(self):
    
        url = 'https://madleet.com/~tim/ctr-groups.txt'
        result = urlfetch.fetch(url)
        if result.status_code != 200:
            self.response.write('Failed to load ctr-groups file')
            return
            
        jsondata = json.loads(result.content)
        
        for group in jsondata:

            newgroup = ranker.Group(name=group['name'])
            newgroup.brf = ranker.Progression(raidname="Blackrock Foundry",
                                              numbosses=10)
            newgroup.hm = ranker.Progression(raidname="Highmaul",
                                             numbosses=6)
            newgroup.toons = group['toons']
        
            # Check if this group already exists in the datastore.  We don't
            # want to overwrite existing progress data for a group if we don't
            # have to.
            query = ranker.Group.query(ranker.Group.name == newgroup.name)
            results = query.fetch(1)
            
            if (len(results) == 0):
                # no results for this group, just insert it into the datastore
                newgroup.put()
            else:
                # only thing to do here is to update the toon list to match
                # what came over in the group data from json
                results[0].toons = newgroup.toons
                if results[0].brf == None:
                    results[0].brf = newgroup.brf
                if results[0].hm == None:
                    results[0].hm = newgroup.hm
                results[0].put()
                
            self.response.write("Added toons for %s<br/>" % newgroup.name)
        
        self.response.write("<br/>")
        self.response.write("Loaded %d groups" % len(jsondata))
                    
# The new Battle.net Mashery API requires an API key when using it.  This
# method stores an API in the datastore so it can used in later page requests.
class SetAPIKey(webapp2.RequestHandler):
    def get(self):

        argkey = self.request.get('key')
        if ((argkey == None) or (len(argkey) == 0)):
            self.response.write("Must pass API with 'key' argument in url")
        else:
            k = ranker.APIKey(key=self.request.get('key'))
            k.put()
            self.response.write("API Key Stored.")

app = webapp2.WSGIApplication([
    ('/', ranker.Display),
    ('/loadgroups', InitDBHandler),
    ('/setapikey', SetAPIKey),
    ('/rank', ranker.Ranker),
], debug=True)
