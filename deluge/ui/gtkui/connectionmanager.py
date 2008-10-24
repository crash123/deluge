#
# connectionmanager.py
#
# Copyright (C) 2007 Andrew Resch ('andar') <andrewresch@gmail.com>
#
# Deluge is free software.
#
# You may redistribute it and/or modify it under the terms of the
# GNU General Public License, as published by the Free Software
# Foundation; either version 3 of the License, or (at your option)
# any later version.
#
# deluge is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with deluge.    If not, write to:
# 	The Free Software Foundation, Inc.,
# 	51 Franklin Street, Fifth Floor
# 	Boston, MA    02110-1301, USA.
#
#    In addition, as a special exception, the copyright holders give
#    permission to link the code of portions of this program with the OpenSSL
#    library.
#    You must obey the GNU General Public License in all respects for all of
#    the code used other than OpenSSL. If you modify file(s) with this
#    exception, you may extend this exception to your version of the file(s),
#    but you are not obligated to do so. If you do not wish to do so, delete
#    this exception statement from your version. If you delete this exception
#    statement from all source files in the program, then also delete it here.

import gtk, gtk.glade
import pkg_resources
import gobject
import socket
import os
import subprocess
import time
import threading

import deluge.component as component
import deluge.xmlrpclib as xmlrpclib
import deluge.common
import deluge.ui.gtkui.common as common
from deluge.ui.client import aclient as client
from deluge.configmanager import ConfigManager
from deluge.log import LOG as log

DEFAULT_CONFIG = {
    "hosts": ["localhost:58846"]
}

HOSTLIST_COL_PIXBUF = 0
HOSTLIST_COL_URI = 1
HOSTLIST_COL_STATUS = 2

HOSTLIST_STATUS = [
    "Offline",
    "Online",
    "Connected"
]

if deluge.common.windows_check():
    import win32api

class ConnectionManager(component.Component):
    def __init__(self):
        component.Component.__init__(self, "ConnectionManager")
        # Get the glade file for the connection manager
        self.glade = gtk.glade.XML(
                    pkg_resources.resource_filename("deluge.ui.gtkui",
                                            "glade/connection_manager.glade"))

        self.window = component.get("MainWindow")
        self.config = ConfigManager("hostlist.conf", DEFAULT_CONFIG)
        self.gtkui_config = ConfigManager("gtkui.conf")
        self.connection_manager = self.glade.get_widget("connection_manager")
        # Make the Connection Manager window a transient for the main window.
        self.connection_manager.set_transient_for(self.window.window)
        self.hostlist = self.glade.get_widget("hostlist")
        self.connection_manager.set_icon(common.get_logo(32))

        self.glade.get_widget("image1").set_from_pixbuf(common.get_logo(32))

        self.liststore = gtk.ListStore(gtk.gdk.Pixbuf, str, int)

        # Holds the online status of hosts
        self.online_status = {}

        # Fill in hosts from config file
        for host in self.config["hosts"]:
            row = self.liststore.append()
            self.liststore.set_value(row, HOSTLIST_COL_URI, host)

        # Setup host list treeview
        self.hostlist.set_model(self.liststore)
        render = gtk.CellRendererPixbuf()
        column = gtk.TreeViewColumn(
            "Status", render, pixbuf=HOSTLIST_COL_PIXBUF)
        self.hostlist.append_column(column)
        render = gtk.CellRendererText()
        column = gtk.TreeViewColumn("Host", render, text=HOSTLIST_COL_URI)
        self.hostlist.append_column(column)

        self.glade.signal_autoconnect({
            "on_button_addhost_clicked": self.on_button_addhost_clicked,
            "on_button_removehost_clicked": self.on_button_removehost_clicked,
            "on_button_startdaemon_clicked": \
                self.on_button_startdaemon_clicked,
            "on_button_close_clicked": self.on_button_close_clicked,
            "on_button_connect_clicked": self.on_button_connect_clicked,
            "on_chk_autoconnect_toggled": self.on_chk_autoconnect_toggled,
            "on_chk_autostart_toggled": self.on_chk_autostart_toggled,
            "on_chk_donotshow_toggled": self.on_chk_donotshow_toggled
        })

        self.connection_manager.connect("delete-event", self.on_delete_event)
        # Connect to the 'changed' event of TreeViewSelection to get selection
        # changes.
        self.hostlist.get_selection().connect("changed",
                                    self.on_selection_changed)

        self.hostlist.connect("row-activated", self._on_row_activated)

        # If classic mode is set, we just start up a localhost daemon and connect to it
        if self.gtkui_config["classic_mode"]:
            uri = "http://localhost:58846"
            self.start_localhost(58846)
            # We need to wait for the host to start before connecting
            while not self.test_online_status(uri):
                time.sleep(0.01)
            client.set_core_uri(uri)
            self.hide()
            return

        # This controls the timer, if it's set to false the update timer will stop.
        self._do_update = True
        self._update_list()

        # Auto connect to a host if applicable
        if self.gtkui_config["autoconnect"] and \
            self.gtkui_config["autoconnect_host_uri"] != None:
            uri = self.gtkui_config["autoconnect_host_uri"]
            # Make sure the uri is proper
            if uri[:7] != "http://":
                uri = "http://" + uri
            if self.test_online_status(uri):
                # Host is online, so lets connect
                client.set_core_uri(uri)
                self.hide()
            elif self.gtkui_config["autostart_localhost"]:
                # Check to see if we are trying to connect to a localhost
                if uri[7:].split(":")[0] == "localhost" or \
                    uri[7:].split(":")[0] == "127.0.0.1":
                    # This is a localhost, so lets try to start it
                    port = uri[7:].split(":")[1]
                    # First add it to the list
                    self.add_host("localhost", port)
                    self.start_localhost(port)
                    # We need to wait for the host to start before connecting
                    while not self.test_online_status(uri):
                        time.sleep(0.01)
                    client.set_core_uri(uri)
                    self.hide()

    def start(self):
        if self.gtkui_config["autoconnect"]:
            # We need to update the autoconnect_host_uri on connection to host
            # start() gets called whenever we get a new connection to a host
            self.gtkui_config["autoconnect_host_uri"] = client.get_core_uri()

    def show(self):
        # Set the checkbuttons according to config
        self.glade.get_widget("chk_autoconnect").set_active(
            self.gtkui_config["autoconnect"])
        self.glade.get_widget("chk_autostart").set_active(
            self.gtkui_config["autostart_localhost"])
        self.glade.get_widget("chk_donotshow").set_active(
            not self.gtkui_config["show_connection_manager_on_start"])

        # Setup timer to update host status
        self._update_timer = gobject.timeout_add(1000, self._update_list)
        self._update_list()
        self._update_list()
        self.connection_manager.show_all()

    def hide(self):
        self.connection_manager.hide()
        self._do_update = False
        try:
            gobject.source_remove(self._update_timer)
        except AttributeError:
            # We are probably trying to hide the window without having it showed
            # first.  OK to ignore.
            pass

    def _update_list(self):
        """Updates the host status"""
        def update_row(model=None, path=None, row=None, columns=None):
            uri = model.get_value(row, HOSTLIST_COL_URI)
            uri = "http://" + uri
            threading.Thread(target=self.test_online_status, args=(uri,)).start()
            try:
                online = self.online_status[uri]
            except:
                online = False

            if online:
                image = gtk.STOCK_YES
                online = HOSTLIST_STATUS.index("Online")
            else:
                image = gtk.STOCK_NO
                online = HOSTLIST_STATUS.index("Offline")

            if uri == current_uri:
                # We are connected to this host, so lets display the connected
                # icon.
                image = gtk.STOCK_CONNECT
                online = HOSTLIST_STATUS.index("Connected")

            pixbuf = self.connection_manager.render_icon(
                image, gtk.ICON_SIZE_MENU)

            model.set_value(row, HOSTLIST_COL_PIXBUF, pixbuf)
            model.set_value(row, HOSTLIST_COL_STATUS, online)

        current_uri = client.get_core_uri()
        self.liststore.foreach(update_row)
        # Update the buttons
        self.update_buttons()

        # See if there is any row selected
        paths = self.hostlist.get_selection().get_selected_rows()[1]
        if len(paths) < 1:
            # And there is at least 1 row
            if self.liststore.iter_n_children(None) > 0:
                # Then select the first row
                self.hostlist.get_selection().select_iter(self.liststore.get_iter_first())
        return self._do_update

    def update_buttons(self):
        """Updates the buttons based on selection"""
        if self.liststore.iter_n_children(None) < 1:
            # There is nothing in the list
            self.glade.get_widget("button_startdaemon").set_sensitive(True)
            self.glade.get_widget("button_connect").set_sensitive(False)
            self.glade.get_widget("button_removehost").set_sensitive(False)
            self.glade.get_widget("image_startdaemon").set_from_stock(
                gtk.STOCK_EXECUTE, gtk.ICON_SIZE_MENU)
            self.glade.get_widget("label_startdaemon").set_text(
                "_Start Daemon")
        self.glade.get_widget("label_startdaemon").set_use_underline(
            True)

        # Get the selected row's URI
        paths = self.hostlist.get_selection().get_selected_rows()[1]
        # If nothing is selected, just return
        if len(paths) < 1:
            return
        row = self.liststore.get_iter(paths[0])
        uri = self.liststore.get_value(row, HOSTLIST_COL_URI)
        status = self.liststore.get_value(row, HOSTLIST_COL_STATUS)

        # Check to see if a localhost is selected
        localhost = False
        if uri.split(":")[0] == "localhost" or uri.split(":")[0] == "127.0.0.1":
            localhost = True

        # Make actual URI string
        uri = "http://" + uri

        # Make sure buttons are sensitive at start
        self.glade.get_widget("button_startdaemon").set_sensitive(True)
        self.glade.get_widget("button_connect").set_sensitive(True)
        self.glade.get_widget("button_removehost").set_sensitive(True)

        # See if this is the currently connected URI
        if status == HOSTLIST_STATUS.index("Connected"):
            # Display a disconnect button if we're connected to this host
            self.glade.get_widget("button_connect").set_label("gtk-disconnect")
            self.glade.get_widget("button_removehost").set_sensitive(False)
        else:
            self.glade.get_widget("button_connect").set_label("gtk-connect")
            if status == HOSTLIST_STATUS.index("Offline") and not localhost:
                self.glade.get_widget("button_connect").set_sensitive(False)

        # Check to see if the host is online
        if status == HOSTLIST_STATUS.index("Connected") \
            or status == HOSTLIST_STATUS.index("Online"):
            self.glade.get_widget("image_startdaemon").set_from_stock(
                gtk.STOCK_STOP, gtk.ICON_SIZE_MENU)
            self.glade.get_widget("label_startdaemon").set_text(
                "_Stop Daemon")

        # Update the start daemon button if the selected host is localhost
        if localhost and status == HOSTLIST_STATUS.index("Offline"):
            # The localhost is not online
            self.glade.get_widget("image_startdaemon").set_from_stock(
                gtk.STOCK_EXECUTE, gtk.ICON_SIZE_MENU)
            self.glade.get_widget("label_startdaemon").set_text(
                "_Start Daemon")

        if not localhost:
            # An offline host
            self.glade.get_widget("button_startdaemon").set_sensitive(False)

        # Make sure label is displayed correctly using mnemonics
        self.glade.get_widget("label_startdaemon").set_use_underline(
            True)

    def save(self):
        """Save the current host list to file"""
        def append_row(model=None, path=None, row=None, columns=None):
            hostlist.append(model.get_value(row, HOSTLIST_COL_URI))

        hostlist = []
        self.liststore.foreach(append_row, hostlist)
        self.config["hosts"] = hostlist
        self.config.save()

    def test_online_status(self, uri):
        """Tests the status of URI.. Returns True or False depending on status.
        """
        online = True
        host = None
        try:
            host = xmlrpclib.ServerProxy(uri.replace("localhost", "127.0.0.1"))
            host.ping()
        except socket.error:
            online = False

        del host
        self.online_status[uri] = online
        return online

    ## Callbacks
    def on_delete_event(self, widget, event):
        self.hide()
        return True

    def on_button_addhost_clicked(self, widget):
        log.debug("on_button_addhost_clicked")
        dialog = self.glade.get_widget("addhost_dialog")
        dialog.set_icon(common.get_logo(16))
        hostname_entry = self.glade.get_widget("entry_hostname")
        port_spinbutton = self.glade.get_widget("spinbutton_port")
        response = dialog.run()
        if response == 1:
            # We add the host
            self.add_host(hostname_entry.get_text(),
                port_spinbutton.get_value_as_int())

        dialog.hide()

    def add_host(self, hostname, port):
        """Adds the host to the list"""
        if hostname.startswith("http://"):
            hostname = hostname[7:]

        # Check to make sure the hostname is at least 1 character long
        if len(hostname) < 1:
            return

        # Get the port and concatenate the hostname string
        hostname = hostname + ":" + str(port)

        # Check to see if there is already an entry for this host and return
        # if thats the case
        self.hosts_liststore = []
        def each_row(model, path, iter, data):
            self.hosts_liststore.append(
                model.get_value(iter, HOSTLIST_COL_URI))
        self.liststore.foreach(each_row, None)
        if hostname in self.hosts_liststore:
            return

        # Host isn't in the list, so lets add it
        row = self.liststore.append()
        self.liststore.set_value(row, HOSTLIST_COL_URI, hostname)
        # Save the host list to file
        self.save()
        # Update the status of the hosts
        self._update_list()

    def on_button_removehost_clicked(self, widget):
        log.debug("on_button_removehost_clicked")
        # Get the selected rows
        paths = self.hostlist.get_selection().get_selected_rows()[1]
        for path in paths:
            self.liststore.remove(self.liststore.get_iter(path))

        # Update the hostlist
        self._update_list()

        # Save the host list
        self.save()

    def on_button_startdaemon_clicked(self, widget):
        log.debug("on_button_startdaemon_clicked")
        if self.liststore.iter_n_children(None) < 1:
            # There is nothing in the list, so lets create a localhost entry
            self.add_host("localhost", 58846)
            # ..and start the daemon.
            self.start_localhost(58846)
            return

        paths = self.hostlist.get_selection().get_selected_rows()[1]
        if len(paths) < 1:
            return
        row = self.liststore.get_iter(paths[0])
        status = self.liststore.get_value(row, HOSTLIST_COL_STATUS)
        uri = self.liststore.get_value(row, HOSTLIST_COL_URI)
        port = uri.split(":")[1]
        if HOSTLIST_STATUS[status] == "Online" or\
            HOSTLIST_STATUS[status] == "Connected":
            # We need to stop this daemon
            uri = "http://" + uri
            # Call the shutdown method on the daemon
            core = xmlrpclib.ServerProxy(uri)
            core.shutdown()
            # Update display to show change
            self.update()
        elif HOSTLIST_STATUS[status] == "Offline":
            self.start_localhost(port)

    def start_localhost(self, port):
        """Starts a localhost daemon"""
        port = str(port)
        log.info("Starting localhost:%s daemon..", port)
        # Spawn a local daemon
        if deluge.common.windows_check():
            win32api.WinExec("deluged -p %s" % port)
        else:
            subprocess.call(["deluged", "-p %s" % port])

    def on_button_close_clicked(self, widget):
        log.debug("on_button_close_clicked")
        self.hide()

    def on_button_connect_clicked(self, widget):
        log.debug("on_button_connect_clicked")
        component.stop()
        paths = self.hostlist.get_selection().get_selected_rows()[1]
        row = self.liststore.get_iter(paths[0])
        status = self.liststore.get_value(row, HOSTLIST_COL_STATUS)
        uri = self.liststore.get_value(row, HOSTLIST_COL_URI)
        # Determine if this is a localhost
        localhost = False
        port = uri.split(":")[1]
        if uri.split(":")[0] == "localhost":
            localhost = True

        uri = "http://" + uri
        if status == HOSTLIST_STATUS.index("Connected"):
            # Stop all the components first.
            component.stop()
            # If we are connected to this host, then we will disconnect.
            client.set_core_uri(None)
            self._update_list()
            return

        # Test the host to see if it is online or not.  We don't use the status
        # column information because it can be up to 5 seconds out of sync.
        if not self.test_online_status(uri):
            log.warning("Host does not appear to be online..")
            # If this is an offline localhost.. lets start it and connect
            if localhost:
                self.start_localhost(port)
                # We need to wait for the host to start before connecting
                while not self.test_online_status(uri):
                    time.sleep(0.01)
                client.set_core_uri(uri)
                self._update_list()
                self.hide()

            # Update the list to show proper status
            self._update_list()

            return

        # Status is OK, so lets change to this host
        client.set_core_uri(uri)
        self.hide()

    def on_chk_autoconnect_toggled(self, widget):
        log.debug("on_chk_autoconnect_toggled")
        value = widget.get_active()
        self.gtkui_config["autoconnect"] = value
        # If we are currently connected to a host, set that as the autoconnect
        # host.
        if client.get_core_uri() != None:
            self.gtkui_config["autoconnect_host_uri"] = client.get_core_uri()

    def on_chk_autostart_toggled(self, widget):
        log.debug("on_chk_autostart_toggled")
        value = widget.get_active()
        self.gtkui_config["autostart_localhost"] = value

    def on_chk_donotshow_toggled(self, widget):
        log.debug("on_chk_donotshow_toggled")
        value = widget.get_active()
        self.gtkui_config["show_connection_manager_on_start"] = not value

    def on_selection_changed(self, treeselection):
        log.debug("on_selection_changed")
        self.update_buttons()

    def _on_row_activated(self, tree, path, view_column):
        self.on_button_connect_clicked(self.glade.get_widget("button_connect"))
