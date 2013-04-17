import datetime
import hashlib
import os
import re
import sys
import tempfile
import urllib
import SOAPpy

if 'TOOLS_PATH' in os.environ:
  sys.path.append(os.environ['TOOLS_PATH']+'/lib')
else:
  sys.path.append('/usr/local/appscale-tools/lib')

from appcontroller_client import AppControllerClient
from local_state import LocalState

from google.appengine.api import users

from secret_key import GLOBAL_SECRET_KEY

class AppHelperException(Exception):
  """ An exception thrown if the requested helper function failed. """
  pass

class AppDashboardHelper:
  """ Helper class to get info from AppScale. """

  # Name of the cookie used for login.
  DEV_APPSERVER_LOGIN_COOKIE = 'dev_appserver_login'

  # IP address of the AppController
  APP_CONTROLLER_IP = '127.0.0.1'

  # Port number of the UserAppServer.
  UA_SERVER_PORT = 4343

  # Port number of the Monitoring service.
  MONITOR_PORT = 8050

  # Regular expression to parse the port from number.
  GET_APP_PORTS_REGEX = ".*\sports: (\d+)[\s|:]"

  # Regular expression to parse the number of ports.
  NUM_PORT_APP_REGEX = ".*num_ports:(\d+)"

  # The charcter that seperates apps.
  APP_DELIMITER = ":"

  # The charcter that seperates users.
  USER_DELIMITER = ":"

  # Regular expression to capture the apps a user is admin of.
  USER_APP_LIST_REGEX = "\napplications:(.*)\n"

  # Regular expression to determine if a user is an admin.
  CLOUD_ADMIN_REGEX = "is_cloud_admin:true"

  # Regular expression to get username from full email.
  USERNAME_FROM_EMAIL_REGEX = '\A(.*)@'

  # Expiration date of the user token.
  TOKEN_EXPIRATION = "20121231120000"

  # Regular expression to get the hashed password from the user data.
  USER_DATA_PASSWORD_REGEX = 'password:([0-9a-fA-F]+)'

  # Regular expression to skip 
  ALL_USERS_NON_USER_REGEX = '^[_]+$'

  # Delimiter to seperate user capabilites.
  USER_CAPABILITIES_DELIMITER = ':'

  def __init__(self, response):
    """ Constructor. 

    Args:
      response: The webapp2 response object of the parent of ths AppDashboard
                object.
    """
    self.server = None
    self.uaserver = None
    self.response = response
    self.cache = {}

  def get_server(self):
    """ Connects to the AppControler and returns the connection handle.

    Returns:
      An AppControllerClient object.
    """
    if self.server is None:
      self.server = AppControllerClient(self.APP_CONTROLLER_IP,\
        GLOBAL_SECRET_KEY)
    return self.server

  def get_uaserver(self):
    """ Connects to the UserAppServer and returns the connection handle

    Returns:
      A SOAPpy object that is connected to the UserAppServer.
    """
    if self.uaserver is None:
      acc = self.get_server()
      uas_host = acc.get_uaserver_host(False)
      self.uaserver = SOAPpy.SOAPProxy('https://%s:%s' % (uas_host,\
        self.UA_SERVER_PORT))
    return self.uaserver

  def get_user_capabilities(self, email):
    """ Query the AppController and return the capabilites of the user.

    Args:
      email: A str containing the email of the user being queried.
    Returns:
      A list of strs containing the capabilities of the user being queried.
    """
    if 'user_caps' in self.cache:
      if email in self.cache['user_caps']:
        return self.cache['user_caps'][email]
    else:
      self.cache['user_caps'] = {}

    try:
      uas = self.get_uaserver()
      caps_list = uas.get_capabilities(email, GLOBAL_SECRET_KEY).split(':')
      self.cache['user_caps'][email] = caps_list
      return caps_list
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.get_user_capabilities() caught "\
        "Exception " + str(type(e)) + ":" + str(e))


  def get_status_info(self):
    """ Query the AppController and get the status informatoin for all the 
        server in the cluster.

    Returns:
      A list of dicts containing the status information on each server.
    """
    try:
      acc = self.get_server()
      node = acc.get_stats()
      return node
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.get_status_info() caught "\
        "Exception " + str(type(e)) + ":" + str(e))
      return []

  def get_host_with_role(self, role):
    """Searches through the local metadata to see which virtual machine runs the
    specified role.

    Args:
      role: A str indicating the role to search for.
    Returns:
      A str containing the host that runs the specified service.
    """
    acc = self.get_server()
    if 'get_role_info' in self.cache:
      node = self.cache['get_role_info']
    else:
      try:
        nodes = acc.get_role_info()
      except Exception as e:
        sys.stderr.write("AppDashboardHelper.get_host_with_role() caught "\
          "Exception " + str(type(e)) + ":" + str(e))
        return ''
    for node in nodes:
      if role in node['jobs']:
        return node['public_ip']

  def get_head_node_ip(self):
    """ Query the AppController and return the ip of the head node. 

    Returns:
      A str containing the ip of the head node.
    """
    return self.get_host_with_role('shadow')

  def get_login_host(self):
    """ Queries the AppController and returns the ip of the login host. 

    Returns:
      A str containing the host that runs the login service.
    """
    return self.get_host_with_role('login')

  def get_monitoring_url(self):
    """ Queries the AppController and returns the url of the monitoring service.

    Returns:
      A str containing the url of the monitoring service.
    """
    return "http://{0}:{1}".format(self.get_head_node_ip(),  self.MONITOR_PORT)


  def get_application_info(self):
    """ Queries the AppController and returns the list of applications running 
        on this cloud.
    
    Returns:
       A dict where the key is the app name, and the value is
       the url of the app (if running) or None (if loading).
    """
    status = self.get_status_info()
    ret = {}
    if len(status) > 0:
      for app in status[0]['apps'].keys():
        if app == 'none':
          break
        if status[0]['apps'][app]:
          try:
            ret[app] = "http://" + self.get_login_host() + ":"\
                + str(self.get_app_port(app))
          except AppHelperException:
            ret[app] = None
        else:
          ret[app] = None
    return ret
 
  def get_database_info(self):
    """ Queries the AppController and returns the database information of this
        cloud.

    Return:
      A dict containing the database information.
    """
    try:
      acc = self.get_server()
      return acc.get_database_information()
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.get_database_info() caught "
        "Exception " + str(type(e)) + ":" + str(e))
      return {}

  def get_service_info(self):
    """ Queries the AppController and returns a list of API services running on
        this cloud.

    Returns:
      A dict where the keys are the names of the services, and the values or the
      status of that service.
    """
    try:
      acc = self.get_server()
      return acc.get_api_status()
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.get_service_info() caught Exception"\
        + str(type(e)) + ":" + str(e))
      return {}

  def get_app_port(self, appname): 
    """ Queries the UserAppServer and returns the port that the app is running
        on.
    
    Args:
      appname: 
        Name of the app being queried.
    Returns:
      An int: the port number.
    Raises:
      AppHelperException if the app has no port.
    """
    try:
      uas = self.get_uaserver()
      app_data = uas.get_app_data(appname, GLOBAL_SECRET_KEY )
      result = re.search(self.GET_APP_PORTS_REGEX, app_data)
      if result:
        # GET_APP_PORTS_REGEX define a capture group, which we use here.
        port = int(result.group(1))
        return port
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.get_app_port() caught "\
        "Exception " + str(type(e)) + ":" + str(e))
    raise AppHelperException("app has no port")

  def upload_app(self, upload_file):
    """ Uploads an App into AppScale.

    Args:
      upload_file: a file object containing the uploaded file data.
    Returns:
      A message reporting the success of the upload.
    Raises:
      AppHelperException reporting the failure of the upload.
    """
    user = users.get_current_user()
    if not user:
      raise AppHelperException("There was an error uploading your application."\
             "  You must be logged in.")
    try:
      tgz_file = tempfile.NamedTemporaryFile()
      tgz_file.write( upload_file.read() )
      tgz_file.close()
      name = tgz_file.name
      acc = self.get_server()
      ret = acc.upload_tgz(name, user.email() )
      if ret == "true":
        return "Application uploaded successfully.  Please wait for the "\
               "application to start running."
      else:
        raise AppHelperException(ret)
    except SOAPpy.Types.faultType as e:  #on success Exception is thrown
      return "Application uploaded successfully.  Please wait for the "\
             "application to start running."
    except SOAPpy.Errors.HTTPError as e:  #on success HTTPError is thrown
      return "Application uploaded successfully.  Please wait for the "\
             "application to start running."
    except Exception as e:
      sys.stderr.write("upload_app() caught Exception " + str(type(e)) + ':'\
        + str(e))
      raise AppHelperException("There was an error uploading your application.")

  def delete_app(self, appname):
    """Instructs AppScale to no longer host the named application.

    Args:
      appname: Name of the app to be removed.
    Returns:
      A str containing a message to be displayed to the user.
    """

    try:
      if not self.does_app_exist(appname):
        return "The given application is not currently running."
      acc = self.get_server()
      ret = acc.stop_app(appname)
      if ret != "true":
        sys.stderr.write("delete_app() AppControler returned: " + ret)
        return "There was an error attempting to remove the application."
    except Exception as e:
      sys.stderr.write("delete_app() caught exception: "+str(e))
      return "There was an error attempting to remove the application."
    return "Application removed successfully. Please wait for your app to shut"\
           " down."

  def does_app_exist(self, appname):
    """Queries the UserAppServer to see if the named application exists.

    Args:
      appname: The name of the app that we should check for existence.
    Returns:
      True if the app does exist, False otherwise.
    """
    try:
      uas = self.get_uaserver()
      app_data = uas.get_app_data(appname, GLOBAL_SECRET_KEY)
      search_data = re.search(self.GET_APP_PORTS_REGEX, app_data)
      if search_data:
        num_ports = int(search_data.group(1))
        if num_ports > 0:
          return True
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.does_app_exist() caught "\
        "Exception " + str(type(e)) + ":" + str(e))
    return False

  def is_user_logged_in(self):
    """ Check if the user is logged in.

    Returns:
      True if the user is logged in, else False.
    """
    user = users.get_current_user()
    if user:
      return True
    return False

  def get_user_email(self):
    """ Get the logged in user's email.

    Returns:
      A str with the user's email, or '' if not found.
    """
    user = users.get_current_user()
    if user:
      return user.email()
    return ''

  def get_user_app_list(self):
    """ Get a list of apps that the current logged in user is an 
        admin of.

    Returns:
      A list of strs, each is the name of an app. 
    """
    user = users.get_current_user()
    if not user:
      return []
    user_data = self.query_user_data(user.email())
    app_resp = re.search(self.USER_APP_LIST_REGEX, user_data)
    if app_resp:
      apps_list = app_resp.group(1).split(self.APP_DELIMITER)
    return apps_list
  def query_user_data(self, email):
    """ Queries the UserAppServer and returns the data on a user.

    Args:
      email: Email address of the user being queried.
    Returns:
      A str contain the the user data, or empty string on error.
    """
    if 'query_user_data' in self.cache:
      if email in self.cache['query_user_data']:
        return self.cache['query_user_data'][email]
    else:
      self.cache['query_user_data'] = {}

    try:
      uaserver = self.get_uaserver()
      user_data =  uaserver.get_user_data(email, GLOBAL_SECRET_KEY)
      self.cache['query_user_data'][email] = user_data
      return user_data
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.query_user_data() caught "\
        "Exception " + str(type(e)) + ":" + str(e))
      return ''

  def is_user_cloud_admin(self):
    """ Check if the logged in user is a cloud admin.

    Returns:
      True or False.
    """
    user = users.get_current_user()
    if not user:
      return False
    email =  user.email()
    user_data = self.query_user_data(email)
    if re.search(self.CLOUD_ADMIN_REGEX, user_data):
      return True
    else:
      return False

  def i_can_upload(self):
    """ Check if the logged in user can upload apps.

    Returns:
      True or False.
    """
    user = users.get_current_user()
    if user:
      if 'upload_app' in self.get_user_capabilities(user.email()):
        return True
    return False

  def create_new_user(self, email, password, account_type='xmpp_user'):
    """ Create new user in the system. 

    Args:
      email: Email address of the new user.
      password: Password for the new user.
    Returns:
      True if the user was created.
    Raises:
      AppHelperException on error.
    """
    try:
      uaserver = self.get_uaserver()
      # First, create the standard account.
      encrypted_pass = LocalState.encrypt_password(email, password)
      result = uaserver.commit_new_user(email, encrypted_pass, account_type,
        GLOBAL_SECRET_KEY)
      if result != 'true':
        raise AppHelperException(result)
  
      # Next, create the XMPP account. if the user's e-mail is a@a.a, then that
      # means their XMPP account name is a@login_ip.
      username_regex = re.compile(self.USERNAME_FROM_EMAIL_REGEX)
      username = username_regex.match(email).groups()[0]
      xmpp_user = "{0}@{1}".format(username,
        self.get_login_host())
      xmpp_pass = LocalState.encrypt_password(xmpp_user, password)
      result = uaserver.commit_new_user(xmpp_user, xmpp_pass, account_type,
        GLOBAL_SECRET_KEY)
      if result != 'true':
        raise AppHelperException(result)
  
      self.create_token(email, email)
      self.set_appserver_cookie(email)
    except AppHelperException as e:
      raise AppHelperException(str(e))
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.create_new_user() caught "\
        "Exception " + str(type(e)) + ":" + str(e))
      raise AppHelperException(str(e))
    return True

  def remove_appserver_cookie(self):
    """ Removes the login cookie. """
    self.response.delete_cookie(self.DEV_APPSERVER_LOGIN_COOKIE)

  def set_appserver_cookie(self, email):
    """ Sets the login cookie.

    Args:
      email: Email of the user to login.
    """
    apps = ''
    user_data =  self.query_user_data(email)
    app_re = re.search(self.USER_APP_LIST_REGEX, user_data)
    if app_re:
      apps_list = app_re.group(1).split(self.APP_DELIMITER)
      apps =  ",".join(apps_list)
    self.response.set_cookie( self.DEV_APPSERVER_LOGIN_COOKIE,
      value=self.get_cookie_value(email, apps),
      expires=datetime.datetime.now() + datetime.timedelta(days=1))

  def get_cookie_value(self, email, apps):
    """ Get the value of the login cookie.
    
    Args:
      email: Email of the user to login.
      apps: List of applications the user is admin of.
    Retuns:
      A str that is the value of the login cookie.
    """
    nick = re.search('^(.*)@', email).group(1)
    hsh = self.get_appengine_hash(email, nick, apps)
    return urllib.quote("{0}:{1}:{2}:{3}".format(email, nick, apps, hsh))

  def get_appengine_hash(self, email, nick, apps):
    """ Encrypt the values and return the hash.

    Args:
      email: Email of the user to login.
      nick: Email of the user to login.
      apps: A str with a comma seperate list of apps the user is an admin of.
    Returns:
      A str that is the hex hash of the input values.
    """
    return hashlib.sha1(email + nick + apps + GLOBAL_SECRET_KEY).hexdigest()

  def create_token(self, token, email):
    """ Create a login token and commit it to the UserAppServer.
    
    Args:
      token: Name of the token to create (usually the email address).
      email: Email of the user to create the login token for.
    """
    try:
      uaserver = self.get_uaserver()
      uaserver.commit_new_token(token, email, self.TOKEN_EXPIRATION, \
        GLOBAL_SECRET_KEY)
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.create_token() caught "\
        "Exception " + str(type(e)) + ":" + str(e))

  def logout_user(self):
    """ Logout the current user. """
    user = users.get_current_user()
    if not user:
      return True
    email = user.email()
    self.create_token('invalid', email)
    self.remove_appserver_cookie()

  def login_user(self, email, password):
    """ Attempt to login the user.

    Args:
      email: Email of the user to login.
      password: Password of the user to login.
    Return:
      True or False if the login succeeded.
    """
    user_data =  self.query_user_data(email) 
    server_re = re.search(self.USER_DATA_PASSWORD_REGEX, user_data)
    if not server_re:
      sys.stderr.write("Failed Login: {0} regex failed".format(email))
      return False
    server_pwd = server_re.group(1)
    encrypted_pass = LocalState.encrypt_password(email, password)
    if server_pwd != encrypted_pass:
      sys.stderr.write("Failed Login: {0} password mismatch".format(email))
      return False
    self.create_token(email, email)
    self.set_appserver_cookie(email)
    return True

  def list_all_users_permisions(self):
    """ Queries the UserAppServer and returns a list of all the users and the 
        permission they have in the system.

    Returns:
      A list of dicts with the email and permissions of each user in the system.
    """
    ret_list = []
    try:
      uas = self.get_uaserver()
      all_users = uas.get_all_users( GLOBAL_SECRET_KEY )
      all_users_list = all_users.split(self.USER_DELIMITER)
      ip = self.get_head_node_ip()
      perm_items = self.get_all_permission_items()
      for usr in all_users_list:
        if re.search('@'+ip+'$', usr): #{ip}\Z/ # skip the XMPP user accounts
          continue 
        if re.search(self.ALL_USERS_NON_USER_REGEX, usr): #skip non users
          continue
        usr_cap = {'email' : usr }
        caps_list = self.get_user_capabilities(usr)
        for perm in perm_items:
          if perm in caps_list:
            usr_cap[perm] = True
          else:
            usr_cap[perm] = False
        ret_list.append(usr_cap)
    except Exception as e:
      sys.stderr.write("AppDashboardHelper.list_all_users_permisions() caught "\
        "Exception " + str(type(e)) + ":" + str(e))
    return ret_list

  def get_all_permission_items(self):
    """ Returns a list of all permission items in the system.
   
    Returns:
      A list of strs with the permission items to display. 
    """
    return ['upload_app']

  def add_user_permissions(self, email, perm):
    """ Add a permission to a user.

    Args: 
      email: Email addres of the user.
      perm: Name of the permission to give to the user.
    Returns: True if the permission was given to the user,
      else False.
    """
    try:
      caps_list = self.get_user_capabilities(email)
      uas = self.get_uaserver()
      new_caps = caps_list
      if perm not in new_caps:
        new_caps.append(perm)
      else:
        return True

      ret = uas.set_capabilities(email, \
        self.USER_CAPABILITIES_DELIMITER.join(new_caps),  GLOBAL_SECRET_KEY)
      if ret == 'true':
        self.cache['user_caps'][email] = new_caps
        return True
      else:
        sys.stderr.write("ERROR: UserAppServer.set_capabilities returned: " + \
          ret)
        return False
    except Exception as e:
      sys.stderr.write("add_user_permissions() caught Exception: " + str(e))
      return False
    return True

  def remove_user_permissions(self, email, perm):
    """ Remove a permission from a user.

    Args: 
      email: Email addres of the user.
      perm: Name of the permission to remove from the user.
    Returns: 
      True if the permission was remove from the user, else False.
    """
    try:
      caps_list = self.get_user_capabilities(email)
      uas = self.get_uaserver()
      new_caps = []
      if perm in caps_list:
        for pitem in caps_list:
          if pitem != perm:
            new_caps.append(pitem)
      else:
        return True 

      ret = uas.set_capabilities(email, \
        self.USER_CAPABILITIES_DELIMITER.join(new_caps),  GLOBAL_SECRET_KEY)
      if ret == 'true':
        self.cache['user_caps'][email] = new_caps
        return True
      else:
        sys.stderr.write("uas.set_capabilities returned: " + ret)
        return False
    except Exception as e:
      sys.stderr.write("remove_user_permissions() caught Exception: " + str(e))
      return False
    return True

