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
import webapp2
import json
import logging
import os
import datetime

# Need this stuff to do oauth with GAE
import httplib2
from oauth2client.client import SignedJwtAssertionCredentials
from apiclient.discovery import build

# need this stuff for the google data API
import gdata.spreadsheets
import gdata.spreadsheets.client
import gdata.gauth
import gdata.alt.appengine

from ctrpmodels import Constants
from ctrpmodels import Group
from ctrpmodels import Raid
from ctrpmodels import Boss

import time
from concurrent import futures
import gc

# Force the deadline for urlfetch to be 10 seconds (Default is 5).  For some
# reason, that first lookup for the spreadsheet takes a bit.
from google.appengine.api import urlfetch
urlfetch.set_default_fetch_deadline(20);

# Grabs the ID for the worksheet from the roster spreadsheet with the
# matching group name.
def getsheetID(feed, name):
    sheet = [x for x in feed.entry if x.title.text.lower() == name.lower()][0]
    id_parts = sheet.id.text.split('/')
    return id_parts[len(id_parts) - 1]

def worker(g, feed, client, curr_key):
    t4 = time.time()
    logging.info('working on group %s' % g)
    sheetID = getsheetID(feed, g)
    sheet = client.GetListFeed(curr_key, sheetID)

    # build up a list of toons for the group from the spreadsheet
    toons = list()

    # each tr is a row in the table.  we care about columns 4-6, which are
    # the character name, the server, and the role.
    for i,entry in enumerate(sheet.entry):

        # get the text from the cells for this row that we care about,
        # assuming none of them are empty
        if entry.get_value('charactername') == None or entry.get_value('server') == None:
            continue

        toon = entry.get_value('charactername').encode('utf-8','ignore')
        if len(toon) == 0:
            continue

        realm = entry.get_value('server').encode('utf-8','ignore')
        if realm != 'Aerie Peak':
            toon += '/%s' % realm
        else:
            toon += '/aerie-peak'

        toons.append(toon)

    toons = sorted(toons)

    t5 = time.time()

    # Check if this group already exists in the datastore.  We don't
    # want to overwrite existing progress data for a group if we don't
    # have to.
    query = Group.query(Group.name == g)
    results = query.fetch(1)

    responsetext = ''
    loggroup = ''
    if (len(results) == 0):
        # create a new group, but only if it has at least 5 toons in
        # it.  that's the threshold for building progress data and
        # there's no real reason to create groups with only that many
        # toons.
        if (len(toons) >= 5):
            newgroup = Group(name=g)
            newgroup.brf = Raid()
            newgroup.brf.bosses = list()
            for boss in Constants.brfbosses:
                newboss = Boss(name = boss)
                newgroup.brf.bosses.append(newboss)
            
            newgroup.hm = Raid()
            newgroup.hm.bosses = list()
            for boss in Constants.hmbosses:
                newboss = Boss(name = boss)
                newgroup.hm.bosses.append(newboss)
            
            newgroup.hfc = Raid()
            newgroup.hfc.bosses = list()
            for boss in Constants.hfcbosses:
                newboss = Boss(name = boss)
                newgroup.hfc.bosses.append(newboss)

            newgroup.toons = toons
            newgroup.rosterupdated = datetime.date.today()
                        
            newgroup.put()
            responsetext = 'Added group %s with %d toons' % (g,len(toons))
            loggroup = 'Added'
        else:
            responsetext = 'New group %s only has %d toons and was not included' % (g, len(toons))
            loggroup = 'Skipped'
    else:
        # the group already exists and all we need to do is update the
        # toon list.  all of the other data stays the same.
        existing = results[0]
        existing.toons = toons
        existing.rosterupdated = datetime.date.today()
        existing.put()
        responsetext = 'Updated group %s with %d toons' % (g,len(toons))
        loggroup = 'Updated'

    t6 = time.time()

    logging.info('time spent getting toons for %s: %s' % (g, (t5-t4)))
    logging.info('time spent updating db for %s: %s' % (g, (t6-t5)))

    return (loggroup, len(toons), responsetext)

class RosterBuilder(webapp2.RequestHandler):

    def get(self):

        self.response.write('<html><head><title>Roster Update</title></head><body>')

        path = os.path.join(os.path.split(__file__)[0],'api-auth.json')
        auth_data = json.load(open(path))

        path = os.path.join(os.path.split(__file__)[0],'oauth_private_key.pem')
        with open(path) as keyfile:
          private_key = keyfile.read()
        
        credentials = SignedJwtAssertionCredentials(
          auth_data['oauth_client_email'],
          private_key,
          scope=(
            'https://www.googleapis.com/auth/drive',
            'https://spreadsheets.google.com/feeds',
            'https://docs.google.com/feeds',
          ))
        http_auth = credentials.authorize(httplib2.Http())
        authclient = build('oauth2','v2',http=http_auth)

        auth2token = gdata.gauth.OAuth2TokenFromCredentials(credentials)

        gd_client = gdata.spreadsheets.client.SpreadsheetsClient()
        gd_client = auth2token.authorize(gd_client)

        logging.info('logged in, grabbing main sheet')

        # Open the main roster feed
        roster_sheet_key = '1tvpsPzZCFupJkTT1y7RmMkuh5VsjBiiA7FvYruJbTtw'
        feed = gd_client.GetWorksheets(roster_sheet_key)
        
        t1 = time.time()
        logging.info('getting group names from dashboard')
        
        groupnames = list()
        lastupdates = list()
        
        # Grab various columns from the DASHBOARD sheet on the spreadsheet, but
        # ignore any groups that are marked as Disbanded.  This is better than
        # looping back through the data again to remove them.
        dashboard_id = getsheetID(feed, 'DASHBOARD')
        dashboard = gd_client.GetListFeed(roster_sheet_key, dashboard_id)
        for entry in dashboard.entry:
            if entry.get_value('teamstatus') != 'Disbanded':
                groupnames.append(entry.get_value('teamname'))
                lastupdates.append(entry.get_value('lastupdate'))

        # sort the lists by the names in the group list.  This is a slick use
        # of zip.  it works by zipping the two lists into a single list of
        # tuples containing the elements, sorting them, then unzipping them
        # back into separate lists again.
        groupnames, lastupdates = (list(t) for t in zip(*sorted(zip(groupnames,lastupdates))))

        print('num groups on dashboard: %d' % len(groupnames))

        t2 = time.time()

        groupcount = 0
        tooncount = 0
        responses = list()

        # Grab the list of groups already in the database.  Loop through and
        # delete any groups that don't exist in the list (it happens...) and
        # any groups that are now marked disbanded.  Groups listed in the
        # history will remain even if they disband.  While we're looping, also
        # remove any groups from the list to be processed that haven't had
        # a roster update since the last time we did this.
        query = Group.query().order(Group.name)
        results = query.fetch()
        for res in results:
            if res.name not in groupnames:
                responses.append(('Removed', 'Removed disbanded or non-existent team from database: %s' % res.name))
                res.key.delete()

            # while we're looping through the groups, also remove any groups
            # from the list to be processed that haven't had a roster update
            # since the last time we parsed groups.
            try:
                index = groupnames.index(res.name)
            except ValueError:
                continue

            lastupdate = datetime.datetime.strptime(lastupdates[index], '%m/%d/%Y').date()
            if res.rosterupdated != None and res.rosterupdated > lastupdate:
                responses.append(('DateUnchanged', '%s hasn\'t been updated since last load (load: %s, update: %s)' % (res.name, res.rosterupdated, lastupdate)))
                groupcount += 1
                tooncount += len(res.toons)
                del groupnames[index]
                del lastupdates[index]

        logging.info('num groups to process: %d' % len(groupnames))

        t3 = time.time()

        logging.info('time spent getting list of groups %s' % (t2-t1))
        logging.info('time spent cleaning groups %s' % (t3-t2))

        # use a threadpoolexecutor from concurrent.futures to gather the group
        # rosters in parallel.  due to the memory limits on GAE, we only allow
        # 25 threads at a time.  this comes *really* close to hitting both the
        # limit on page-load time and the limit on memory.
        executor = futures.ThreadPoolExecutor(max_workers=15)

        fs = dict()
        for g in groupnames:
            fs[executor.submit(worker, g, feed, gd_client, roster_sheet_key)] = g

        for future in futures.as_completed(fs):
            g = fs[future]
            if future.exception() is not None:
                logging.info('%s generated an exception: %s' % (g, future.exception()))
            else:
                returnval = future.result()
                responses.append((returnval[0], returnval[2]))
                if returnval[0] == 'Added' or returnval[0] == 'Updated':
                    groupcount += 1
                    tooncount += returnval[1]
        fs.clear()

        self.response.write('<h3>New Raid Groups</h3>')
        added = sorted([x for x in responses if x[0] == 'Added'], key=lambda tup: tup[1])
        for i in added:
            self.response.write('%s<br/>' % i[1])

        self.response.write('<h3>Updated Raid Groups</h3>')
        updated = sorted([x for x in responses if x[0] == 'Updated'], key=lambda tup: tup[1])
        for i in updated:
            self.response.write('%s<br/>' % i[1])

        self.response.write('<h3>Disbanded/Removed Raid Groups</h3>')
        removed = sorted([x for x in responses if x[0] == 'Removed'], key=lambda tup: tup[1])
        for i in removed:
            self.response.write('%s<br/>' % i[1])

        self.response.write('<h3>Raid groups skipped due to Size</h3>')
        skipped = sorted([x for x in responses if x[0] == 'Skipped'], key=lambda tup: tup[1])
        for i in skipped:
            self.response.write('%s<br/>' % i[1])

        self.response.write('<h3>Raid groups skipped due to Last Update Date</h3>')
        updatedate = sorted([x for x in responses if x[0] == 'DateUnchanged'], key=lambda tup: tup[1])
        for i in updatedate:
            self.response.write('%s<br/>' % i[1])

        t6 = time.time()
        logging.info('time spent building groups %s' % (t6-t3))

        self.response.write('<br/>')
        self.response.write('Now managing %d groups with %d total toons<br/>' % (groupcount, tooncount))

        self.response.write('</body></html>')
