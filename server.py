#!/usr/bin/env python3

# coding=utf-8
# pylint: disable=broad-except,unused-argument,line-too-long, unused-variable
# Copyright (c) 2016-2018, F5 Networks, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import csv
import json
import logging
import os
import requests
import sys
import threading
import time

from flask import Flask, flash, request, redirect, url_for

VERSION = '05042020-1'

CSV_FILE = '/tmp/list.csv'
LOG_FILE = '/tmp/logoutput.txt'

LOG = logging.getLogger('csv_invite_processor')
LOG.setLevel(logging.DEBUG)
FORMATTER = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
LOGSTREAM = logging.StreamHandler(sys.stdout)
LOGSTREAM.setFormatter(FORMATTER)
LOG.addHandler(LOGSTREAM)

USERNAME = None
PASSWORD = None

API_HOST = "api.cloudservices.f5.com"
API_VERSION = "v1"

ROLE_ID = "r-NAYFdYfiR"

DRY_RUN = False

DELAY = 0

TOKEN = None


def get_service_token():
    if USERNAME and PASSWORD:
        try:
            headers = {
                "Content-Type": "application/json"
            }
            data = {
                "username": USERNAME,
                "password": PASSWORD
            }
            url = "https://%s/%s/svc-auth/login" % (API_HOST, API_VERSION)
            response = requests.post(
                url, headers=headers, data=json.dumps(data))
            if response.status_code < 300:
                return response.json()['access_token']
            else:
                LOG.error('error retrieving token: %d: %s',
                          response.status_code, response.content)
        except Exception as ex:
            LOG.error('error retrieveing token: %s', ex)
        return None
    else:
        LOG.error('can not issue token without setting Usename and Password')
        return None


def get_account_info(token):
    if token:
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % token
            }
            url = "https://%s/%s/svc-account/user" % (API_HOST, API_VERSION)
            response = requests.get(url, headers=headers)
            if response.status_code < 300:
                data = response.json()
                return {
                    'user_id': data['id'],
                    'account_id': data['primary_account_id']
                }
            else:
                LOG.error('error retrieving account: %d: %s',
                          response.status_code, response.content)
        except Exception as ex:
            LOG.error('error retrieveing account: %s', ex)
    else:
        LOG.error('can not retrieve user account without access token')
    return None


def get_existing_invites(token):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % token
        }
        url = "https://%s/%s/svc-account/invites" % (API_HOST, API_VERSION)
        response = requests.get(url, headers=headers)
        if response.status_code < 300:
            return response.json()
        else:
            LOG.error('error retrieving existing invitations: %d: %s',
                      response.status_code, response.content)
    except Exception as ex:
        LOG.error('error retrieveing account invitations: %s', ex)
    return None


def delete_invite(token, invite_id):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % token
        }
        url = "https://%s/%s/svc-account/invites/%s" % (
            API_HOST, API_VERSION, invite_id)
        response = requests.delete(url, headers=headers)
        if response.status_code < 300:
            return True
        else:
            LOG.error('error deleting invitation: %s - %d: %s',
                      invite_id, response.status_code, response.content)
    except Exception as ex:
        LOG.error('error deleting invitations: %s - %s', invite_id, ex)


def delete_accepted_invitations(token, account_id):
    existing_invitations = get_existing_invites(token)
    if existing_invitations:
        for invite in existing_invitations['invites']:
            if invite['status'] == 'accepted' and invite['inviter_account_id'] == account_id:
                if DRY_RUN:
                    LOG.info(
                        'dry run - would have deleted accepted invitation for %s', invite['invitee_email'])
                else:
                    LOG.info('deleting accepted invitation for %s',
                             invite['invitee_email'])
                    delete_invite(token, invite('invite_id'))


def get_existing_account_members(token, account_id):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % token
        }
        url = "https://%s/%s/svc-account/accounts/%s/members" % (
            API_HOST, API_VERSION, account_id)
        response = requests.get(url, headers=headers)
        if response.status_code < 300:
            return response.json()
        else:
            LOG.error('error retrieving existing account members: %d: %s',
                      response.status_code, response.content)
    except Exception as ex:
        LOG.error('error retrieveing existing account members: %s', ex)
    return None


def issue_invite(token, account_id, user_id, first_name, last_name, email):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % token
        }
        data = {
            "inviter_account_id": account_id,
            "inviter_user_id": user_id,
            "account_ids": [
                account_id
            ],
            "invitees": [
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email
                }
            ],
            "role_id": ROLE_ID
        }
        url = "https://%s/%s/svc-account/invites" % (API_HOST, API_VERSION)
        response = requests.post(url, headers=headers, data=json.dumps(data))
        if response.status_code < 300:
            return response.json()
        else:
            LOG.error('error sending invitation for: %s - %d: %s',
                      email, response.status_code, response.content)
    except Exception as ex:
        LOG.error('error sending invitations: %s - %s', email, ex)


class ListProcessingThread(object):

    def __init__(self):
        thread = threading.Thread(target=self.run, args=())
        thread.daemon = True
        thread.start()

    def run(self):
        LOG.debug('logging into F5 cloud services')
        token = get_service_token()
        if not token:
            LOG.error('halting processing due to login failure')
            LOG.info('finished processing %d records', 0)
            return False
        account_info = get_account_info(token)
        if not account_info:
            LOG.error('halting processing missing account ID')
            LOG.info('finished processing %d records', 0)
            return False
        if DRY_RUN:
            LOG.info('performing dry run simulation only')
        LOG.info('deleting accepted invitations for users in account: %s',
                 account_info['user_id'])
        delete_accepted_invitations(token, account_info['account_id'])
        LOG.info('getting existing account members for account: %s',
                 account_info['account_id'])
        sent_invites = []
        members = get_existing_account_members(
            token, account_info['account_id'])['users']
        for member in members:
            sent_invites.append(member['user']['email'])
        LOG.info('sending invites with user_id: %s, account id: %s',
                 account_info['user_id'], account_info['account_id'])
        existing_invitations = get_existing_invites(token)
        if existing_invitations:
            for invite in existing_invitations['invites']:
                if not invite['invitee_email'] in sent_invites and invite['status'] == 'pending':
                    sent_invites.append(invite['invitee_email'])
        else:
            LOG.warning('no existing invitations')
        line_count = 0
        with open(CSV_FILE, newline='') as csvfile:
            try:
                invitations = csv.reader(csvfile, dialect='excel')
                for row in invitations:
                    line_count += 1
                    first_name = row[0]
                    last_name = row[1]
                    email = row[2]
                    if first_name == 'FirstName':
                        continue
                    if not email in sent_invites:
                        if DRY_RUN:
                            LOG.info(
                                'dry run - would have processed invitation for %s %s: %s', first_name, last_name, email)
                        else:
                            LOG.info('processing invitation for %s %s: %s',
                                     first_name, last_name, email)
                            issue_invite(
                                token, account_info['account_id'], account_info['user_id'], first_name, last_name, email)
                        if DELAY > 0:
                            time.sleep(DELAY)
                    else:
                        LOG.info('invitation for %s %s: %s already processed',
                                 first_name, last_name, email)
            except Exception as ex:
                LOG.error('error reading CSV: %s', ex)
        LOG.info('finished processing %d invitations', line_count)


app = Flask(__name__)


@app.route('/', methods=['GET', 'POST'])
def upload_list():
    global USERNAME, PASSWORD, API_HOST, API_VERSION, ROLE_ID, DRY_RUN, DELAY
    if request.method == 'POST':
        if 'username' not in request.form:
            flash('No username')
            return redirect(request.url)
        if 'password' not in request.form:
            flash('No password')
            return redirect(request.url)
        if 'file' not in request.files:
            flash('No file')
            return redirect(request.url)
        USERNAME = request.form['username']
        PASSWORD = request.form['password']
        if 'apihost' in request.form:
            API_HOST = request.form['apihost']
        if 'apiversion' in request.form:
            API_VERSION = request.form['apiversion']
        if 'roleid' in request.form:
            ROLE_ID = request.form['roleid']
        if 'dryrun' in request.form and request.form['dryrun'] == 'on':
            DRY_RUN = True
        else:
            DRY_RUN = False
        if 'delay' in request.form:
            delay = request.form['delay']
            DELAY = round((int(delay) / 1000), 2)
        file = request.files['file']
        if file.filename == '':
            flash('No file')
            return redirect(request.url)
        if file:
            handlers = [h for h in LOG.handlers if not isinstance(
                h, logging.StreamHandler)]
            for handler in handlers:
                LOG.removeHandler(handler)
            if os.path.exists(LOG_FILE):
                os.unlink(LOG_FILE)
            if os.path.exists(CSV_FILE):
                os.unlink(CSV_FILE)
            textstream = logging.FileHandler(LOG_FILE)
            textstream.setFormatter(FORMATTER)
            LOG.addHandler(textstream)
            file.save(CSV_FILE)
            LOG.info('received %s', file.filename)
            num_lines = sum(1 for line in open(CSV_FILE))
            LOG.info('processing %d lines', num_lines)
            LOG.info('starting list processing thread...')
            csv_processor = ListProcessingThread()
            return redirect(url_for('display_stream'))
    return '''
    <!doctype html>
    <html>
    <head>
    <title>Upload CSV List</title>
    </head>
    <body>
    <h1>Invite with Excel Format CSV List</h1>
    <h2>
    Version: %s
    </h2>
    <pre>
    FirstName,LastName,email
    Bob,Johnson,bob.johnson@f5.com
    Mike,Smith,mike.smith@f5.com
    Don,Williams,don.williams@f5.com
    Scott,White,scott.white@f5.com
    Justin,Case,justin.case@f5.com
    </pre>
    <form method=post enctype=multipart/form-data>
      <table>
      <tr><th align='left'>API Host: </th><td><input name=apihost value=api.cloudservices.f5.com></td></tr>
      <tr><th align='left'>API Version: </th><td><input name=apiversion value=v1></td></tr>
      <tr><th align='left'>Username: </th><td><input name=username></td></tr>
      <tr><th align='left'>Password: </th><td><input name=password></td></tr>
      <tr><th align='left'>Invite as Role ID: </th><td><input name=roleid value=r-NAYFdYfiR></td></tr>
      <tr><th align='left'>Dry Run: </th><td><input name=dryrun type=checkbox unchecked></td></tr>
      <tr><th align='left'>Delay Between Invites (ms): </th><td><input type=number name=delay min=0 max=10000 value=1000></td></tr>
      <tr><th align='left'>CSV Invite File: </th><td><input type=file name=file></td></tr>
      </table>
      </br>
      <input type=submit value=Process>
    </form>
    </body>
    </html>
    ''' % VERSION


@app.route('/display_stream')
def display_stream():
    return '''
    <!doctype html>
    <html>
    <head>
    <title>Processing the CSV List</title>
    </head>
    <body>
    <p>Last Record: <span id="latest"></span></p>
    <p>Output Log:</p>
    <ul id="output"></ul>
    <script>
        var latest = document.getElementById('latest');
        var output = document.getElementById('output');
        var position = 0;
        var stop_timer = false;

        function handleNewData() {
            var xhr = new XMLHttpRequest();
            xhr.open('GET', '/stream_output');
            xhr.send();
            xhr.onload = function() {
                var messages = xhr.responseText.split('\\n');
                messages.slice(position, -1).forEach(function(value) {
                    console.log(value.includes('finished processing'));
                    if(value.includes('finished processing')) {
                        stop_timer = true;
                        latest.textContent = 'Done';
                    } else {
                        latest.textContent = value;
                        var item = document.createElement('li');
                        item.textContent = value;
                        output.appendChild(item);
                    }
                });
                position = messages.length - 1;
            }
        }
        var timer;
        timer = setInterval(function() {
            handleNewData();
            if (stop_timer) {
                clearInterval(timer);
                latest.textContent = 'Done';
            }
        }, 1000);
    </script>    
    </body>
    </html>
    '''


@app.route('/stream_output')
def stream():
    def generate():
        with open(LOG_FILE, 'r') as log_out:
            yield log_out.read()
    return app.response_class(generate(), mimetype='text/plain')


app.run(host='0.0.0.0', threaded=True)
