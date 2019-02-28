#!/usr/bin/python

"""
File: gcexport.py
Author: Kyle Krafka (https://github.com/kjkjava/)
Date: April 28, 2015

Description:	Use this script to export your fitness data from Garmin Connect.
				See README.md for more information.
"""

from urllib import urlencode
from math import floor
from sets import Set
from datetime import datetime, timedelta, tzinfo
from getpass import getpass
from sys import argv
from os.path import isdir
from os.path import isfile
from os import mkdir
from os import remove
from os import stat
from xml.dom.minidom import parseString
from subprocess import call

import glob
import shutil

import urllib, urllib2, cookielib, json, re
from fileinput import filename

import argparse
import zipfile

script_version = '1.0.0'
current_date = datetime.now().strftime('%Y-%m-%d')
activities_directory = './' + current_date + '_garmin_connect_export'

parser = argparse.ArgumentParser()

# TODO: Implement verbose and/or quiet options.
# parser.add_argument('-v', '--verbose', help="increase output verbosity", action="store_true")
parser.add_argument('--version', help="print version and exit", action="store_true")
parser.add_argument('--username', help="your Garmin Connect username (otherwise, you will be prompted)", nargs='?')
parser.add_argument('--password', help="your Garmin Connect password (otherwise, you will be prompted)", nargs='?')

parser.add_argument('-c', '--count', nargs='?', default="1",
	help="number of recent activities to download, or 'all' (default: 1)")

parser.add_argument('-f', '--format', nargs='?', choices=['gpx', 'tcx', 'original'], default="gpx",
	help="export format; can be 'gpx', 'tcx', or 'original' (default: 'gpx')")

parser.add_argument('-d', '--directory', nargs='?', default=activities_directory,
	help="the directory to export to (default: './YYYY-MM-DD_garmin_connect_export')")

parser.add_argument('-u', '--unzip',
	help="if downloading ZIP files (format: 'original'), unzip the file and removes the ZIP file",
	action="store_true")

args = parser.parse_args()

if args.version:
	print argv[0] + ", version " + script_version
	exit(0)

cookie_jar = cookielib.CookieJar()
opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookie_jar))
# print cookie_jar

# url is a string, post is a dictionary of POST parameters, headers is a dictionary of headers.
def http_req(url, post=None, headers={}):
	request = urllib2.Request(url)
	# request.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/1337 Safari/537.36')  # Tell Garmin we're some supported browser.
	request.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2816.0 Safari/537.36')  # Tell Garmin we're some supported browser.
	for header_key, header_value in headers.iteritems():
		request.add_header(header_key, header_value)
	if post:
		# print "POSTING"
		post = urlencode(post)  # Convert dictionary to POST parameter string.
	# print request.headers
	# print cookie_jar
	# print post
	# print request
	response = opener.open(request, data=post)  # This line may throw a urllib2.HTTPError.

	# N.B. urllib2 will follow any 302 redirects. Also, the "open" call above may throw a urllib2.HTTPError which is checked for below.
	# print response.getcode()
	if response.getcode() == 204:
		# For activities without GPS coordinates, there is no GPX download (204 = no content).
		# Write an empty file to prevent redownloading it.
		print 'Writing empty file since there was no GPX activity data...'
		return ''
	elif response.getcode() != 200:
		raise Exception('Bad return code (' + str(response.getcode()) + ') for: ' + url)

	return response.read()

def absentOrNull(element, a):
	if not a:
		return True
	elif element not in a:
		return True
	elif a[element]:
		return False
	else:
		return True

def fromActivitiesOrDetail(element, a, detail, detailContainer):
	if absentOrNull(detailContainer, detail) or absentOrNull(element, detail[detailContainer]):
		return None if absentOrNull(element, a) else a[element]
	else:
		return details[detailContainer][element]

def trunc6(f):
	return "{0:12.6f}".format(floor(f*1000000)/1000000).lstrip()

# A class building tzinfo objects for fixed-offset time zones.
# (copied from https://docs.python.org/2/library/datetime.html)
class FixedOffset(tzinfo):
    """Fixed offset in minutes east from UTC."""

    def __init__(self, offset, name):
        self.__offset = timedelta(minutes = offset)
        self.__name = name

    def utcoffset(self, dt):
        return self.__offset

    def tzname(self, dt):
        return self.__name

    def dst(self, dt):
        return timedelta(0)

# build an 'aware' datetime from two 'naive' datetime objects (that is timestamps
# as present in the activities.json), using the time difference as offset
def offsetDateTime(timeLocal, timeGMT):
	localDT = datetime.strptime(timeLocal, "%Y-%m-%d %H:%M:%S")
	gmtDT = datetime.strptime(timeGMT, "%Y-%m-%d %H:%M:%S")
	offset = localDT - gmtDT
	offsetTz = FixedOffset(offset.seconds/60, "LCL")
	return localDT.replace(tzinfo=offsetTz)

def hhmmssFromSeconds(sec):
	return str(timedelta(seconds=int(sec))).zfill(8)

# this is almost the datetime format Garmin used in the activity-search-service
# JSON 'display' fields (Garmin didn't zero-pad the date and the hour, but %d and %H do)
ALMOST_RFC_1123 = "%a, %d %b %Y %H:%M"

# map the numeric parentTypeId to its name for the CSV output
parent_type_id = {
	1: 'running',
	2: 'cycling',
	3: 'hiking',
	4: 'other',
	9: 'walking',
	17: 'any activity type',
	26: 'swimming',
	29: 'fitness equipment',
	71: 'motorcycling',
	83: 'transition',
	144: 'diving',
	149: 'yoga' }

# typeId values using pace instead of speed
uses_pace = Set([1, 3, 9]) # running, hiking, walking

def paceOrSpeedRaw(typeId, parentTypeId, mps):
	kmh = 3.6 * mps
	if (typeId in uses_pace) or (parentTypeId in uses_pace):
		return 60 / kmh
	else:
		return kmh

def paceOrSpeedFormatted(typeId, parentTypeId, mps):
	kmh = 3.6 * mps
	if (typeId in uses_pace) or (parentTypeId in uses_pace):
		# format seconds per kilometer as MM:SS, see https://stackoverflow.com/a/27751293
		return '{0:02d}:{1:02d}'.format(*divmod(int(round(3600 / kmh)), 60))
	else:
		return "{0:.1f}".format(round(kmh, 1))


print 'Welcome to Garmin Connect Exporter!'

# Create directory for data files.
if isdir(args.directory):
	print 'Warning: Output directory already exists. Will skip already-downloaded files and append to the CSV file.'

username = args.username if args.username else raw_input('Username: ')
password = args.password if args.password else getpass()

# Maximum number of activities you can request at once.
# Used to be 100 and enforced by Garmin for older endpoints; for the current endpoint 'url_gc_search'
# the limit is not known (I have less than 1000 activities and could get them all in one go)
limit_maximum = 1000

max_tries = 3

WEBHOST = "https://connect.garmin.com"
REDIRECT = "https://connect.garmin.com/post-auth/login"
BASE_URL = "http://connect.garmin.com/en-US/signin"
GAUTH = "http://connect.garmin.com/gauth/hostname"
SSO = "https://sso.garmin.com/sso"
CSS = "https://static.garmincdn.com/com.garmin.connect/ui/css/gauth-custom-v1.2-min.css"

data = {'service': REDIRECT,
    'webhost': WEBHOST,
    'source': BASE_URL,
    'redirectAfterAccountLoginUrl': REDIRECT,
    'redirectAfterAccountCreationUrl': REDIRECT,
    'gauthHost': SSO,
    'locale': 'en_US',
    'id': 'gauth-widget',
    'cssUrl': CSS,
    'clientId': 'GarminConnect',
    'rememberMeShown': 'true',
    'rememberMeChecked': 'false',
    'createAccountShown': 'true',
    'openCreateAccount': 'false',
    'usernameShown': 'false',
    'displayNameShown': 'false',
    'consumeServiceTicket': 'false',
    'initialFocus': 'true',
    'embedWidget': 'false',
    'generateExtraServiceTicket': 'false'}

print urllib.urlencode(data)

# URLs for various services.
url_gc_login     = 'https://sso.garmin.com/sso/login?' + urllib.urlencode(data)
url_gc_post_auth = 'https://connect.garmin.com/modern/activities?'
url_gc_summary   = 'https://connect.garmin.com/proxy/activity-search-service-1.2/json/activities?start=0&limit=1'
url_gc_search    = 'https://connect.garmin.com/modern/proxy/activitylist-service/activities/search/activities?'
url_gc_activity  = 'https://connect.garmin.com/modern/proxy/activity-service/activity/'
url_gc_device    = 'https://connect.garmin.com/modern/proxy/device-service/deviceservice/app-info/'
url_gc_gpx_activity = 'https://connect.garmin.com/modern/proxy/download-service/export/gpx/activity/'
url_gc_tcx_activity = 'https://connect.garmin.com/modern/proxy/download-service/export/tcx/activity/'
url_gc_original_activity = 'http://connect.garmin.com/proxy/download-service/files/activity/'

# Initially, we need to get a valid session cookie, so we pull the login page.
print 'Request login page'
http_req(url_gc_login)
print 'Finish login page'

# Now we'll actually login.
post_data = {'username': username, 'password': password, 'embed': 'true', 'lt': 'e1s1', '_eventId': 'submit', 'displayNameRequired': 'false'}  # Fields that are passed in a typical Garmin login.
print 'Post login data'
login_response = http_req(url_gc_login, post_data)
print 'Finish login post'

# extract the ticket from the login response
pattern = re.compile(r".*\?ticket=([-\w]+)\";.*", re.MULTILINE|re.DOTALL)
match = pattern.match(login_response)
if not match:
	raise Exception('Did not get a ticket in the login response. Cannot log in. Did you enter the correct username and password?')
login_ticket = match.group(1)
print 'login ticket=' + login_ticket

print 'Request authentication'
# print url_gc_post_auth + 'ticket=' + login_ticket
http_req(url_gc_post_auth + 'ticket=' + login_ticket)
print 'Finished authentication'

# We should be logged in now.
if not isdir(args.directory):
	mkdir(args.directory)

csv_filename = args.directory + '/activities.csv'
csv_existed = isfile(csv_filename)

csv_file = open(csv_filename, 'a')

# Write header to CSV file
if not csv_existed:
	csv_file.write('Activity name,\
		Description,\
		Begin timestamp,\
		Duration (h:m:s),\
		Moving duration (h:m:s),\
		Distance (km),\
		Average speed (km/h or min/km),\
		Average moving speed (km/h or min/km),\
		Max. speed (km/h or min/km),\
		Elevation loss uncorrected (m),\
		Elevation gain uncorrected (m),\
		Elevation min. uncorrected (m),\
		Elevation max. uncorrected (m),\
		Min. heart rate (bpm),\
		Max. heart rate (bpm),\
		Average heart rate (bpm),\
		Calories,\
		Avg. cadence (rpm),\
		Max. cadence (rpm),\
		Strokes,\
		Avg. temp (C),\
		Min. temp (C),\
		Max. temp (C),\
		Map,\
		End timestamp,\
		Begin timestamp (ms),\
		End timestamp (ms),\
		Device,\
		Activity type,\
		Event type,\
		Time zone,\
		Begin latitude (DD),\
		Begin longitude (DD),\
		End latitude (DD),\
		End longitude (DD),\
		Elevation gain corrected (m),\
		Elevation loss corrected (m),\
		Elevation max. corrected (m),\
		Elevation min. corrected (m),\
		Sample count\n')

download_all = False
if args.count == 'all':
	# If the user wants to download all activities, first download one,
	# then the result of that request will tell us how many are available
	# so we will modify the variables then.
	total_to_download = 1
	download_all = True
else:
	total_to_download = int(args.count)
total_downloaded = 0

device_dict = dict()

# This while loop will download data from the server in multiple chunks, if necessary.
while total_downloaded < total_to_download:
	# Maximum of 100... 400 return status if over 100.  So download 100 or whatever remains if less than 100.
	if total_to_download - total_downloaded > 100:
		num_to_download = 10
	else:
		num_to_download = total_to_download - total_downloaded

	search_params = {'start': total_downloaded, 'limit': num_to_download}
	# Query Garmin Connect
	print "Making activity request ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
	print url_gc_search + urlencode(search_params)
	result = http_req(url_gc_search + urlencode(search_params))
	print "Finished activity request ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"

	# Persist JSON
	json_filename = args.directory + '/activities.json'
	json_file = open(json_filename, 'a')
	json_file.write(result)
	json_file.close()

	json_results = json.loads(result)  # TODO: Catch possible exceptions here.

	# search = json_results['results']['search']

	if download_all:
		# Modify total_to_download based on how many activities the server reports.
		total_to_download = int(json_results['results']['totalFound'])

		# Do it only once.
		download_all = False

	# Pull out just the list of activities.
	activities = json_results

	# Process each activity.
	for a in activities:
		# Display which entry we're working on.
		print 'Garmin Connect activity: [' + str(a['activityId']) + ']',
		print a['activityName']
		
		activity_details = None
		details = None
		tries = max_tries
		while tries > 0:
			activity_details = http_req(url_gc_activity + str(a['activityId']))
			details = json.loads(activity_details)
			# I observed a failure to get a complete JSON detail in about 5-10 calls out of 1000
			# retrying then statistically gets a better JSON ;-)
			if len(details['summaryDTO']) > 0:
				tries = 0
			else:
				print 'retrying for ' + str(a['activityId'])
				tries -= 1
				if tries == 0:
					raise Exception('Didn\'t get "summaryDTO" after ' + str(max_tries) + ' tries for ' + str(a['activityId']))

		parentTypeId = 4 if absentOrNull('activityType', a) else a['activityType']['parentTypeId']
		typeId = 4 if absentOrNull('activityType', a) else a['activityType']['typeId']

		startTimeWithOffset = offsetDateTime(a['startTimeLocal'], a['startTimeGMT'])

		elapsedDuration = details['summaryDTO']['elapsedDuration'] if details['summaryDTO'] else None
		duration = elapsedDuration if elapsedDuration else a['duration']
		durationSeconds = int(round(duration))
		endTimeWithOffset = startTimeWithOffset + timedelta(seconds=durationSeconds) if duration else None

		# get some values from detail if present, from a otherwise
		startLatitude = fromActivitiesOrDetail('startLatitude', a, details, 'summaryDTO')
		startLongitude = fromActivitiesOrDetail('startLongitude', a, details, 'summaryDTO')
		endLatitude = fromActivitiesOrDetail('endLatitude', a, details, 'summaryDTO')
		endLongitude = fromActivitiesOrDetail('endLongitude', a, details, 'summaryDTO')

		print '\t' + startTimeWithOffset.isoformat() + ',',
		if 'duration' in a:
			print hhmmssFromSeconds(a['duration']) + ',',
		else:
			print '??:??:??,',
		if 'distance' in a:
			print "{0:.3f}".format(a['distance']/1000)
		else:
			print '0.000 km'

		# try to get the device details (and cache them, as they're used for multiple activities)
		device = None
		device_app_inst_id = None if absentOrNull('metadataDTO', details) else details['metadataDTO']['deviceApplicationInstallationId']
		if device_app_inst_id:
			if not (device_dict.has_key(device_app_inst_id)):
				# print '\tGetting device details ' + str(device_app_inst_id)
				device_details = http_req(url_gc_device + str(device_app_inst_id))
				device_filename = args.directory + '/device_' + str(device_app_inst_id) + '.json'
				device_file = open(device_filename, 'a')
				device_file.write(device_details)
				device_file.close()
				device_dict[device_app_inst_id] = None if not device_details else json.loads(device_details)
			device = device_dict[device_app_inst_id]


		date = datetime.fromtimestamp(a['beginTimestamp'] / 1e3)

		newDirectory = args.directory + str(date.year) + "-Semana" + str(date.isocalendar()[1])
		if not isdir(newDirectory):
			mkdir(newDirectory)

		if args.format == 'gpx':
			data_filename = newDirectory + '/activity_' + str(a['activityId']) + '.gpx'
			download_url = url_gc_gpx_activity + str(a['activityId']) + '?full=true'
			file_mode = 'w'
		elif args.format == 'tcx':
			data_filename = newDirectory + '/activity_' + str(a['activityId']) + '.tcx'
			download_url = url_gc_tcx_activity + str(a['activityId']) + '?full=true'
			file_mode = 'w'
		elif args.format == 'original':
			data_filename = newDirectory + '/activity_' + str(a['activityId']) + '.zip'
			fit_filename = newDirectory + '/' + str(a['activityId']) + '.fit'
			download_url = url_gc_original_activity + str(a['activityId'])
			file_mode = 'wb'
		else:
			raise Exception('Unrecognized format.')

		if isfile(data_filename):
			print '\tData file already exists; skipping...'
			continue
		if args.format == 'original' and isfile(fit_filename):  # Regardless of unzip setting, don't redownload if the ZIP or FIT file exists.
			print '\tFIT data file already exists; skipping...'
			continue

		# Download the data file from Garmin Connect.
		# If the download fails (e.g., due to timeout), this script will die, but nothing
		# will have been written to disk about this activity, so just running it again
		# should pick up where it left off.
		print '\tDownloading file...',

		try:
			data = http_req(download_url)
		except urllib2.HTTPError as e:
			# Handle expected (though unfortunate) error codes; die on unexpected ones.
			if e.code == 500 and args.format == 'tcx':
				# Garmin will give an internal server error (HTTP 500) when downloading TCX files if the original was a manual GPX upload.
				# Writing an empty file prevents this file from being redownloaded, similar to the way GPX files are saved even when there are no tracks.
				# One could be generated here, but that's a bit much. Use the GPX format if you want actual data in every file,
				# as I believe Garmin provides a GPX file for every activity.
				print 'Writing empty file since Garmin did not generate a TCX file for this activity...',
				data = ''
			elif e.code == 404 and args.format == 'original':
				# For manual activities (i.e., entered in online without a file upload), there is no original file.
				# Write an empty file to prevent redownloading it.
				print 'Writing empty file since there was no original activity data...',
				data = ''
			else:
				raise Exception('Failed. Got an unexpected HTTP error (' + str(e.code) + download_url +').')

		save_file = open(data_filename, file_mode)
		save_file.write(data)
		save_file.close()

		# Write stats to CSV.
		empty_record = '"",'

		csv_record = ''

		csv_record += empty_record if absentOrNull('activityName', a) else '"' + a['activityName'].replace('"', '""') + '",'
		csv_record += empty_record if absentOrNull('description', a) else '"' + a['description'].replace('"', '""') + '",'
		csv_record += '"' + startTimeWithOffset.strftime(ALMOST_RFC_1123) + '",'
		# csv_record += '"' + startTimeWithOffset.isoformat() + '",'
		csv_record += empty_record if not duration else hhmmssFromSeconds(round(duration)) + ','
		csv_record += empty_record if absentOrNull('summaryDTO', details) or absentOrNull('movingDuration', details['summaryDTO']) else hhmmssFromSeconds(details['summaryDTO']['movingDuration']) + ','
		csv_record += empty_record if absentOrNull('distance', a) else '"' + "{0:.5f}".format(a['distance']/1000) + '",'
		csv_record += empty_record if absentOrNull('averageSpeed', a) else '"' + trunc6(paceOrSpeedRaw(typeId, parentTypeId, a['averageSpeed'])) + '",'
		csv_record += empty_record if absentOrNull('summaryDTO', details) or absentOrNull('averageMovingSpeed', details['summaryDTO']) else '"' + trunc6(paceOrSpeedRaw(typeId, parentTypeId, details['summaryDTO']['averageMovingSpeed'])) + '",'
		csv_record += empty_record if absentOrNull('summaryDTO', details) or absentOrNull('maxSpeed', details['summaryDTO']) else '"' + trunc6(paceOrSpeedRaw(typeId, parentTypeId, details['summaryDTO']['maxSpeed'])) + '",'
		csv_record += empty_record if a['elevationCorrected'] or absentOrNull('summaryDTO', details) or absentOrNull('elevationLoss', details['summaryDTO']) else '"' + str(round(details['summaryDTO']['elevationLoss'], 2)) + '",'
		csv_record += empty_record if a['elevationCorrected'] or absentOrNull('summaryDTO', details) or absentOrNull('elevationGain', details['summaryDTO']) else '"' + str(round(details['summaryDTO']['elevationGain'], 2)) + '",'
		csv_record += empty_record if a['elevationCorrected'] or absentOrNull('summaryDTO', details) or absentOrNull('minElevation', details['summaryDTO']) else '"' + str(round(details['summaryDTO']['minElevation'], 2)) + '",'
		csv_record += empty_record if a['elevationCorrected'] or absentOrNull('summaryDTO', details) or absentOrNull('maxElevation', details['summaryDTO']) else '"' + str(round(details['summaryDTO']['maxElevation'], 2)) + '",'
		csv_record += empty_record # no minimum heart rate in JSON
		csv_record += empty_record if absentOrNull('maxHR', a) else '"' + "{0:.0f}".format(a['maxHR']) + '",'
		csv_record += empty_record if absentOrNull('averageHR', a) else '"' + "{0:.0f}".format(a['averageHR']) + '",'
		csv_record += empty_record if absentOrNull('summaryDTO', details) or absentOrNull('calories', details['summaryDTO']) else '"' + "{0:.0f}".format(details['summaryDTO']['calories']) + '",'
		csv_record += empty_record if absentOrNull('averageBikingCadenceInRevPerMinute', a) else '"' + str(a['averageBikingCadenceInRevPerMinute']) + '",'
		csv_record += empty_record if absentOrNull('maxBikingCadenceInRevPerMinute', a) else '"' + str(a['maxBikingCadenceInRevPerMinute']) + '",'
		csv_record += empty_record if absentOrNull('strokes', a) else '"' + str(a['strokes']) + '",'
		csv_record += empty_record # no WeightedMeanAirTemperature in JSON
		csv_record += empty_record if absentOrNull('minTemperature', a) else '"' + str(a['minTemperature']) + '",'
		csv_record += empty_record if absentOrNull('maxTemperature', a) else '"' + str(a['maxTemperature']) + '",'
		csv_record += '"https://connect.garmin.com/modern/activity/' + str(a['activityId']) + '",'
		csv_record += empty_record if not endTimeWithOffset else '"' + endTimeWithOffset.strftime(ALMOST_RFC_1123) + '",'
		# csv_record += empty_record if not endTimeWithOffset else '"' + endTimeWithOffset.isoformat() + '",'
		csv_record += empty_record if absentOrNull('beginTimestamp', a) else '"' + str(a['beginTimestamp']) + '",'
		csv_record += empty_record if absentOrNull('beginTimestamp', a) else '"' + str(a['beginTimestamp']+durationSeconds*1000) + '",'
		csv_record += empty_record if absentOrNull('productDisplayName', device) else '"' + device['productDisplayName'].replace('"', '""') + ' ' + device['versionString'] + '",'
		csv_record += empty_record if absentOrNull('activityType', a) else '"' + a['activityType']['typeKey'] + '",'
		csv_record += empty_record if absentOrNull('eventType', a) else '"' + a['eventType']['typeKey'] + '",'
		csv_record += '"' + startTimeWithOffset.isoformat()[-6:] + '",'
		csv_record += empty_record if not startLatitude else '"' + trunc6(startLatitude) + '",'
		csv_record += empty_record if not startLongitude else '"' + trunc6(startLongitude) + '",'
		csv_record += empty_record if not endLatitude else '"' + trunc6(endLatitude) + '",'
		csv_record += empty_record if not endLongitude else '"' + trunc6(endLongitude) + '",'
		csv_record += empty_record if not a['elevationCorrected'] or absentOrNull('summaryDTO', details) or absentOrNull('elevationGain', details['summaryDTO']) else '"' + str(round(details['summaryDTO']['elevationGain'], 2)) + '",'
		csv_record += empty_record if not a['elevationCorrected'] or absentOrNull('summaryDTO', details) or absentOrNull('elevationLoss', details['summaryDTO']) else '"' + str(round(details['summaryDTO']['elevationLoss'], 2)) + '",'
		csv_record += empty_record if not a['elevationCorrected'] or absentOrNull('summaryDTO', details) or absentOrNull('maxElevation', details['summaryDTO']) else '"' + str(round(details['summaryDTO']['maxElevation'], 2)) + '",'
		csv_record += empty_record if not a['elevationCorrected'] or absentOrNull('summaryDTO', details) or absentOrNull('minElevation', details['summaryDTO']) else '"' + str(round(details['summaryDTO']['minElevation'], 2)) + '",'
		csv_record += '""'         # no Sample Count in JSON
		csv_record += '\n'

		csv_file.write(csv_record.encode('utf8'))

		if args.format == 'gpx':
			# Validate GPX data. If we have an activity without GPS data (e.g., running on a treadmill),
			# Garmin Connect still kicks out a GPX, but there is only activity information, no GPS data.
			# N.B. You can omit the XML parse (and the associated log messages) to speed things up.
			gpx = parseString(data)
			gpx_data_exists = len(gpx.getElementsByTagName('trkpt')) > 0

			if gpx_data_exists:
				print 'Done. GPX data saved.'
			else:
				print 'Done. No track points found.'
		elif args.format == 'original':
			if args.unzip and data_filename[-3:].lower() == 'zip':  # Even manual upload of a GPX file is zipped, but we'll validate the extension.
				print "Unzipping and removing original files...",
				print 'Filesize is: ' + str(stat(data_filename).st_size)
				if stat(data_filename).st_size > 0:
					zip_file = open(data_filename, 'rb')
					z = zipfile.ZipFile(zip_file)
					for name in z.namelist():
						z.extract(name, newDirectory)
					zip_file.close()
				else:
					print 'Skipping 0Kb zip file.'
				remove(data_filename)
			print 'Done.'
		else:
			# TODO: Consider validating other formats.
			print 'Done.'
	total_downloaded += num_to_download
# End while loop for multiple chunks.

csv_file.close()

for filename in glob.iglob(args.directory + '/**Semana**/*.fit'):
	print(filename)
	shutil.copy2(filename, args.directory + '/todos/')

print 'Done!'
