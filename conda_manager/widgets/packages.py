# -*- coding:utf-8 -*-
#
# Copyright © 2015 The Spyder Development Team
# Copyright © 2014 Gonzalo Peña-Castellanos (@goanpeca)
#
# Licensed under the terms of the MIT License

"""
Conda Packager Manager Widget.
"""

# Standard library imports
from __future__ import (absolute_import, division, print_function,
                        with_statement)
from collections import deque
import json
import gettext
import os.path as osp
import sys

# Third party imports
from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import (QComboBox, QDialogButtonBox, QDialog,
                            QHBoxLayout, QLabel, QMessageBox, QPushButton,
                            QProgressBar, QSpacerItem, QVBoxLayout, QWidget)

# Local imports
from conda_manager.api import ManagerAPI
from conda_manager.utils import get_conf_path, get_module_data_path
from conda_manager.utils import constants as C
from conda_manager.utils.logs import logger
from conda_manager.utils.py3compat import configparser as cp
from conda_manager.widgets.search import SearchLineEdit
from conda_manager.widgets.table import CondaPackagesTable
from conda_manager.widgets.dialogs import (ChannelsDialog,
                                           CondaPackageActionDialog)


_ = gettext.gettext


class CondaPackagesWidget(QWidget):
    """Conda Packages Widget."""
    # Location of updated repo.json files from continuum/binstar
    CONDA_CONF_PATH = get_conf_path('repo')

    # Location of continuum/anaconda default repos shipped with conda-manager
    DATA_PATH = get_module_data_path()

    # file inside DATA_PATH with metadata for conda packages
    DATABASE_FILE = 'packages.ini'

    sig_worker_ready = Signal()
    sig_packages_ready = Signal()
    sig_environment_created = Signal()
    sig_channels_updated = Signal(tuple, tuple)  # channels, active_channels

    def __init__(self,
                 parent,
                 name=None,
                 prefix=None,
                 channels=(),
                 active_channels=(),
                 conda_url='https://conda.anaconda.org',
                 conda_api_url='https://api.anaconda.org',
                 setup=True,
                 data_directory=None):

        super(CondaPackagesWidget, self).__init__(parent)

        # Check arguments: active channels, must be witbhin channels
        for ch in active_channels:
            if ch not in channels:
                raise Exception("'active_channels' must be also within "
                                "'channels'")

        if data_directory is None:
            data_directory = self.CONDA_CONF_PATH

        self._parent = parent
        self._hide_widgets = False
        self._metadata = {}        # From repo.continuum
        self._metadata_links = {}  # Bundled metadata
        self.api = ManagerAPI()
        self.busy = False
        self.data_directory = data_directory
        self.conda_url = conda_url
        self.conda_api_url = conda_api_url
        self.name = name
        self.prefix = prefix
        self.root_prefix = self.api.ROOT_PREFIX
        self.message = ''

        if channels:
            self._channels = channels
            self._active_channels = active_channels
        else:
            self._channels = self.api.conda_get_condarc_channels()
            self._active_channels = self._channels[:]

        # Widgets
        self.bbox = QDialogButtonBox(Qt.Horizontal)
        self.button_cancel = QPushButton('')
        self.button_channels = QPushButton(_('Channels'))
        self.button_ok = QPushButton(_('Ok'))
        self.button_update = QPushButton(_('Update package index'))
        self.button_apply = QPushButton(_('Apply'))
        self.button_clear = QPushButton(_('Clear'))
        self.combobox_filter = QComboBox(self)
        self.progress_bar = QProgressBar(self)
        self.status_bar = QLabel(self)
        self.table = CondaPackagesTable(self)
        self.textbox_search = SearchLineEdit(self)
        self.widgets = [self.button_update, self.button_channels,
                        self.combobox_filter, self.textbox_search, self.table,
                        self.button_ok]

        # Widgets setup
        max_height = self.status_bar.fontMetrics().height()
        max_width = self.textbox_search.fontMetrics().width('M'*23)
        self.bbox.addButton(self.button_ok, QDialogButtonBox.ActionRole)
        self.button_ok.setAutoDefault(True)
        self.button_ok.setDefault(True)
        self.button_ok.setMaximumSize(QSize(0, 0))
        self.button_ok.setVisible(False)
        self.button_cancel.setIcon(QIcon.fromTheme("process-stop"))
        self.button_cancel.setFixedWidth(max_height*2)
        self.button_channels.setCheckable(True)
        self.combobox_filter.addItems([k for k in C.COMBOBOX_VALUES_ORDERED])
        self.combobox_filter.setMinimumWidth(120)
        self.progress_bar.setMaximumHeight(max_height*1.2)
        self.progress_bar.setMaximumWidth(max_height*12)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        self.setMinimumSize(QSize(480, 300))
        self.setWindowTitle(_("Conda Package Manager"))
        self.status_bar.setFixedHeight(max_height*1.5)
        self.textbox_search.setMaximumWidth(max_width)

        # Layout
        spacer_w = 250
        spacer_h = 5

        top_layout = QHBoxLayout()
        top_layout.addWidget(self.combobox_filter)
        top_layout.addWidget(self.button_channels)
        top_layout.addWidget(self.button_update)
        top_layout.addWidget(self.textbox_search)
        top_layout.addStretch()

        middle_layout = QVBoxLayout()
        middle_layout.addWidget(self.table)

        actions_layout = QHBoxLayout()
        actions_layout.addStretch()
        actions_layout.addWidget(self.button_apply)
        actions_layout.addWidget(self.button_clear)

        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(self.status_bar, Qt.AlignLeft)
        bottom_layout.addWidget(self.progress_bar, Qt.AlignRight)
        bottom_layout.addWidget(self.button_cancel, Qt.AlignRight)

        layout = QVBoxLayout(self)
        layout.addItem(QSpacerItem(spacer_w, spacer_h))
        layout.addLayout(top_layout)
        layout.addLayout(middle_layout)
        layout.addLayout(actions_layout)
        layout.addItem(QSpacerItem(spacer_w, spacer_h))
        layout.addLayout(bottom_layout)
        layout.addItem(QSpacerItem(spacer_w, spacer_h/2))

        self.setLayout(layout)

        self.setTabOrder(self.combobox_filter, self.button_channels)
        self.setTabOrder(self.button_channels, self.button_update)
        self.setTabOrder(self.button_update, self.textbox_search)
        self.setTabOrder(self.textbox_search, self.table)

        # Signals and slots
        self.api.sig_repodata_updated.connect(self._repodata_updated)
        self.combobox_filter.currentIndexChanged.connect(self.filter_package)
        self.button_apply.clicked.connect(self._handle_multiple_actions)
        self.button_clear.clicked.connect(self.table.clear_actions)
        self.button_cancel.clicked.connect(self.cancel_process)
        self.button_channels.clicked.connect(self.show_channels_dialog)
        self.button_update.clicked.connect(self.update_package_index)
        self.textbox_search.textChanged.connect(self.search_package)
        self.table.sig_conda_action_requested.connect(self._run_conda_action)
        self.table.sig_actions_updated.connect(self.update_actions)
        self.table.sig_pip_action_requested.connect(self._run_pip_action)
        self.table.sig_status_updated.connect(self.update_status)

        # Setup
        self.api.client_set_domain(conda_api_url)
        self.api.set_data_directory(self.data_directory)
        self._load_bundled_metadata()
        self.update_actions(0)
        if setup:
            self.set_environment(name=name, prefix=prefix)
            self.setup()

    # --- Helpers/Callbacks
    # -------------------------------------------------------------------------
    def update_actions(self, number_of_actions):
        """
        """
        self.button_apply.setVisible(bool(number_of_actions))
        self.button_clear.setVisible(bool(number_of_actions))

    def _load_bundled_metadata(self):
        """
        """
        parser = cp.ConfigParser()
        db_file = CondaPackagesWidget.DATABASE_FILE
        with open(osp.join(self.DATA_PATH, db_file)) as f:
            parser.readfp(f)

        for name in parser.sections():
            metadata = {}
            for key, data in parser.items(name):
                metadata[key] = data
            self._metadata_links[name] = metadata

    def _setup_packages(self, worker, data, error):
        """
        """
        self.table.setup_model(worker.packages, data, self._metadata_links)
        self.combobox_filter.setCurrentIndex(0)
        self.filter_package(C.INSTALLED)

        if error:
            self.update_status(error, False)

    def _prepare_model_data(self, worker=None, output=None, error=None):
        """
        """
        packages, apps = output
        worker = self.api.pip_list(prefix=self.prefix)
        worker.sig_finished.connect(self._pip_list_ready)
        worker.packages = packages
        worker.apps = apps

    def _pip_list_ready(self, worker, pip_packages, error):
        """
        """
        packages = worker.packages
        linked_packages = self.api.conda_linked(prefix=self.prefix)
        worker = self.api.client_prepare_packages_data(packages,
                                                       linked_packages,
                                                       pip_packages)
        worker.packages = packages
        worker.sig_finished.connect(self._setup_packages)

    def _repodata_updated(self, paths):
        """
        """
        worker = self.api.client_load_repodata(paths, extra_data={},
                                               metadata=self._metadata)
        worker.paths = paths
        worker.sig_finished.connect(self._prepare_model_data)

    def _metadata_updated(self, url, path):
        """
        """
        with open(path, 'r') as f:
            data = f.read()
        try:
            self._metadata = json.loads(data)
        except Exception:
            self._metadata = {}
        self.api.update_repodata(self._channels)

    # ---
    # -------------------------------------------------------------------------
    def _handle_multiple_actions(self):
        """
        """
        logger.debug('')

        prefix = self.prefix

        if prefix == self.root_prefix:
            name = 'root'
        elif self.api.environment_exists(prefix=prefix):
            name = osp.basename(prefix)
        else:
            name = prefix

        actions = self.table.get_actions()
        self._multiple_process = deque()

        pip_actions = actions[C.PIP_PACKAGE]
        conda_actions = actions[C.CONDA_PACKAGE]

        pip_remove = pip_actions.get(C.ACTION_REMOVE, [])
        conda_remove = conda_actions.get(C.ACTION_REMOVE, [])
        conda_install = conda_actions.get(C.ACTION_INSTALL, [])
        conda_upgrade = conda_actions.get(C.ACTION_UPGRADE, [])
        conda_downgrade = conda_actions.get(C.ACTION_DOWNGRADE, [])

        message = ''
        template_1 = '<li><b>{0}={1}</b></li>'
        template_2 = '<li><b>{0}: {1} -> {2}</b></li>'

        if pip_remove:
            temp = [template_1.format(i['name'], i['version_to']) for i in
                    pip_remove]
            message += ('The following pip packages will be removed: '
                        '<ul>' + ''.join(temp) + '</ul>')
        if conda_remove:
            temp = [template_1.format(i['name'], i['version_to']) for i in
                    conda_remove]
            message += ('<br>The following conda packages will be removed: '
                        '<ul>' + ''.join(temp) + '</ul>')
        if conda_install:
            temp = [template_1.format(i['name'], i['version_to']) for i in
                    conda_install]
            message += ('<br>The following conda packages will be installed: '
                        '<ul>' + ''.join(temp) + '</ul>')
        if conda_downgrade:
            temp = [template_2.format(
                    i['name'], i['version_from'], i['version_to']) for i in
                    conda_downgrade]
            message += ('<br>The following conda packages will be downgraded: '
                        '<ul>' + ''.join(temp) + '</ul>')
        if conda_upgrade:
            temp = [template_2.format(
                    i['name'], i['version_from'], i['version_to']) for i in
                    conda_upgrade]
            message += ('<br>The following conda packages will be upgraded: '
                        '<ul>' + ''.join(temp) + '</ul>')
        message += '<br>'
        reply = QMessageBox.question(self,
                                     'Proceed with the following actions?',
                                     message,
                                     buttons=QMessageBox.Ok |
                                     QMessageBox.Cancel)

        if reply == QMessageBox.Ok:
            # Pip remove
            for pkg in pip_remove:
                status = (_('Removing pip package <b>') + pkg['name'] +
                          '</b>' + _(' from <i>') + name + '</i>')
                pkgs = [pkg['name']]

                def trigger(prefix=prefix, pkgs=pkgs):
                    return lambda: self.api.pip_remove(prefix=prefix,
                                                       pkgs=pkgs)

                self._multiple_process.append([status, trigger()])

            # Process conda actions
            if conda_remove:
                status = (_('Removing conda packages <b>') +
                          '</b>' + _(' from <i>') + name + '</i>')
                pkgs = [i['name'] for i in conda_remove]

                def trigger(prefix=prefix, pkgs=pkgs):
                    return lambda: self.api.conda_remove(pkgs=pkgs,
                                                         prefix=prefix)
                self._multiple_process.append([status, trigger()])

            if conda_install:
                pkgs = ['{0}={1}'.format(i['name'], i['version_to']) for i in
                        conda_install]

                status = (_('Installing conda packages <b>') +
                          '</b>' + _(' on <i>') + name + '</i>')

                def trigger(prefix=prefix, pkgs=pkgs):
                    return lambda: self.api.conda_install(prefix=prefix,
                                                          pkgs=pkgs)
                self._multiple_process.append([status, trigger()])

            # Conda downgrade
            if conda_downgrade:
                status = (_('Downgrading conda packages <b>') +
                          '</b>' + _(' on <i>') + name + '</i>')

                pkgs = ['{0}={1}'.format(i['name'], i['version_to']) for i in
                        conda_downgrade]

                def trigger(prefix=prefix, pkgs=pkgs):
                    return lambda: self.api.conda_install(prefix=prefix,
                                                          pkgs=pkgs)
                self._multiple_process.append([status, trigger()])

            # Conda update
            if conda_upgrade:
                status = (_('Upgrading conda packages <b>') +
                          '</b>' + _(' on <i>') + name + '</i>')

                pkgs = ['{0}={1}'.format(i['name'], i['version_to']) for i in
                        conda_upgrade]

                def trigger(prefix=prefix, pkgs=pkgs):
                    return lambda: self.api.conda_install(prefix=prefix,
                                                          pkgs=pkgs)
                self._multiple_process.append([status, trigger()])

            self._multiple_process
            self._run_multiple_actions()

    def _run_multiple_actions(self, worker=None, output=None, error=None):
        """
        """
        if self._multiple_process:
            status, func = self._multiple_process.popleft()
            self.update_status(status)
            worker = func()
            worker.sig_finished.connect(self._run_multiple_actions)
        else:
            self.update_status('', hide=False)
            self.setup()

    def _pip_process_ready(self, worker, output, error):
        """
        """
        if error is not None:
            status = _('there was an error')
            self.update_status(hide=False, message=status)
        else:
            self.update_status(hide=True)

        self.setup()

    def _conda_process_ready(self, worker, output, error):
        """
        """
        if error is not None:
            status = _('there was an error')
            self.update_status(hide=False, message=status)
        else:
            self.update_status(hide=True)

        dic = self._temporal_action_dic

        if dic['action'] == C.ACTION_CREATE:
            self.sig_environment_created.emit()

        self.setup()

    def _run_pip_action(self, package_name, action):
        """
        """
        prefix = self.prefix

        if prefix == self.root_prefix:
            name = 'root'
        elif self.api.conda_environment_exists(prefix=prefix):
            name = osp.basename(prefix)
        else:
            name = prefix

        if action == C.ACTION_REMOVE:
            msgbox = QMessageBox.question(self,
                                          "Remove pip package: "
                                          "{0}".format(package_name),
                                          "Do you want to proceed?",
                                          QMessageBox.Yes | QMessageBox.No)
            if msgbox == QMessageBox.Yes:
                self.update_status()
                worker = self.api.pip_remove(prefix=self.prefix,
                                             pkgs=[package_name])
                worker.sig_finished.connect(self._pip_process_ready)
                status = (_('Removing pip package <b>') + package_name +
                          '</b>' + _(' from <i>') + name + '</i>')
                self.update_status(hide=True, message=status,
                                   progress=[0, 0])

    def _run_conda_action(self, package_name, action, version, versions,
                          packages_sizes):
        """ """
        prefix = self.prefix
        dlg = CondaPackageActionDialog(self, prefix, package_name, action,
                                       version, versions, packages_sizes,
                                       self._active_channels)

        if dlg.exec_():
            dic = {}

            self.status = 'Processing'
            self.update_status(hide=True)
            self.repaint()

            ver1 = dlg.label_version.text()
            ver2 = dlg.combobox_version.currentText()
            pkg = u'{0}={1}{2}'.format(package_name, ver1, ver2)
            dep = dlg.checkbox.checkState()
            state = dlg.checkbox.isEnabled()
            dlg.close()

            dic['pkg'] = pkg
            dic['dep'] = not (dep == 0 and state)
            dic['action'] = None
            self._run_conda_process(action, dic)

    def _run_conda_process(self, action, dic):
        """ """
        prefix = self.prefix

        if prefix == self.root_prefix:
            name = 'root'
        elif self.api.conda_environment_exists(prefix=prefix):
            name = osp.basename(prefix)
        else:
            name = prefix

        if 'pkg' in dic and 'dep' in dic:
            pkgs = dic['pkg']
            if not isinstance(pkgs, list):
                pkgs = [pkgs]
            dep = dic['dep']

        if (action == C.ACTION_INSTALL or action == C.ACTION_UPGRADE or
           action == C.ACTION_DOWNGRADE):
            status = _('Installing <b>') + dic['pkg'] + '</b>'
            status = status + _(' into <i>') + name + '</i>'
            worker = self.api.conda_install(prefix=prefix, pkgs=pkgs, dep=dep,
                                            channels=self._active_channels)
        elif action == C.ACTION_REMOVE:
            status = (_('Removing <b>') + dic['pkg'] + '</b>' +
                      _(' from <i>') + name + '</i>')
            worker = self.api.conda_remove(pkgs[0], prefix=prefix)

        # --- Environment management actions
        elif action == C.ACTION_CREATE:
            status = _('Creating environment <b>') + name + '</b>'
            worker = self.api.conda_create(prefix=prefix, pkgs=pkgs,
                                           channels=self._active_channels)
        elif action == C.ACTION_CLONE:
            status = (_('Cloning ') + '<i>' + dic['cloned from'] +
                      _('</i> into <b>') + name + '</b>')
        elif action == C.ACTION_REMOVE_ENV:
            status = _('Removing environment <b>') + name + '</b>'

        worker.sig_finished.connect(self._conda_process_ready)
        self.update_status(hide=True, message=status, progress=None)
        self._temporal_action_dic = dic

    # Public API
    # -------------------------------------------------------------------------
    def setup(self):
        """
        Setup packages.

        Downloads repodata, loads repodata, prepares and updates model data.
        """
        logger.debug('')

        self.update_status('Updating package index', True)
        worker = self.api.update_metadata()
        worker.sig_download_finished.connect(self._metadata_updated)

    def prepare_model_data(self, packages, apps):
        """
        """
        logger.debug('')
        self._prepare_model_data(output=(packages, apps))

    def update_status(self, message=None, hide=True, progress=None,
                      env=False):
        """
        Update status bar, progress bar display and widget visibility

        message : str
            Message to display in status bar.
        hide : bool
            Enable/Disable widgets.
        progress : [int, int]
            Show status bar progress. [0, 0] means spinning statusbar.
        """
        self.busy = hide
        for widget in self.widgets:
            widget.setDisabled(hide)

        self.progress_bar.setVisible(hide)
        self.button_cancel.setVisible(hide)

        if message is not None:
            self.message = message

        if self.prefix == self.root_prefix:
            short_env = 'root'
#        elif self.api.environment_exists(prefix=self.prefix):
#            short_env = osp.basename(self.prefix)
        else:
            short_env = self.prefix

        if env:
            self.message = '{0} (<b>{1}</b>)'.format(
                self.message, short_env,
                )
        self.status_bar.setText(self.message)

#        if progress is not None:
#            self.progress_bar.setMinimum(0)
#            self.progress_bar.setMaximum(progress[1])
#            self.progress_bar.setValue(progress[0])
#        else:
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(0)

    def show_channels_dialog(self):
        """
        Show the channels dialog.
        """
        button_channels = self.button_channels
        self.dlg = ChannelsDialog(self,
                                  channels=self._channels,
                                  active_channels=self._active_channels,
                                  conda_url=self.conda_url)
        button_channels.setDisabled(True)
        self.dlg.sig_channels_updated.connect(self.update_channels)
        self.dlg.rejected.connect(lambda: button_channels.setEnabled(True))
        self.dlg.rejected.connect(button_channels.toggle)
        self.dlg.rejected.connect(button_channels.setFocus)
        self.dlg.accepted.connect(self.accept_channels_dialog)
        self.dlg.show()

        geo_tl = button_channels.geometry().topLeft()
        tl = button_channels.parentWidget().mapToGlobal(geo_tl)
        x = tl.x() + 2
        y = tl.y() + button_channels.height()
        self.dlg.move(x, y)
        self.dlg.button_add.setFocus()

    def accept_channels_dialog(self):
        self.button_channels.setFocus()
        self.button_channels.toggle()

    def update_channels(self, channels, active_channels):
        """
        """
        logger.debug(str((channels, active_channels)))

        if sorted(self._active_channels) != sorted(active_channels) or \
                sorted(self._channels) != sorted(channels):
            self._channels = channels
            self._active_channels = active_channels
            self.sig_channels_updated.emit(tuple(channels),
                                           tuple(active_channels))
            self.setup()

    def update_package_index(self):
        """ """
        self.setup()

    def search_package(self, text):
        """ """
        self.table.search_string_changed(text)

    def filter_package(self, value):
        """ """
        self.table.filter_status_changed(value)

    def set_environment(self, name=None, prefix=None, update=True):
        """ """
        logger.debug(str((name, prefix, update)))

#        if name and prefix:
#            raise Exception('#TODO:')

        if prefix and self.api.conda_environment_exists(prefix=prefix):
            self.prefix = prefix
        elif name and self.api.conda_environment_exists(name=name):
            self.prefix = self.get_prefix_envname(name)
        else:
            self.prefix = self.root_prefix

        self.setup()
#        # Reset environent to reflect this environment in the package model
#        if update:
#            self.setup_packages()

    def get_environment_prefix(self):
        """
        Returns the active environment prefix.
        """
        return self._prefix

    def get_environment_name(self):
        """
        Returns the active environment name if it is located in the default
        conda environments directory, otherwise it returns the prefix.
        """
        name = osp.basename(self._prefix)

        if not (name and self.api.environment_exists(name=name)):
            name = self._prefix

        return name

    def get_environments(self):
        """
        Get a list of conda environments located in the default conda
        environments directory.
        """
        return self.api.conda_get_envs()

    def get_prefix_envname(self, name):
        """
        Returns the prefix for a given environment by name.
        """
        return self.api.conda_get_prefix_envname(name)

    def get_package_versions(self, name):
        """ """
        return self.table.source_model.get_package_versions(name)

    def create_environment(self, name=None, prefix=None, packages=['python']):
        """ """
        # If environment exists already? GUI should take care of this
        # BUT the api call should simply set that env as the env
        dic = {}
        dic['name'] = name
        dic['pkg'] = packages
        dic['dep'] = True  # Not really needed but for the moment!
        dic['action'] = C.CREATE
        self._run_conda_process(C.CREATE, dic)

    def enable_widgets(self):
        """ """
        self.table.hide_columns()

    def disable_widgets(self):
        """ """
        self.table.hide_action_columns()

    def cancel_process(self):
        """
        Allow user to cancel an ongoing process.
        """
        logger.debug(str('process canceled by user.'))
        if self.busy:
            answer = QMessageBox.question(
                self,
                'Stop Conda Manager?',
                'Conda is still busy.\n\nDo you want to stop the process?',
                buttons=QMessageBox.Yes | QMessageBox.No)

            if answer == QMessageBox.Yes:
                self.api.conda_terminate()
                self.update_status(hide=False, message='Process cancelled')
        else:
            QDialog.reject(self)


class CondaPackagesDialog(QDialog, CondaPackagesWidget):
    """
    Conda packages dialog.

    Dialog version of the CondaPackagesWidget.
    """
    sig_worker_ready = Signal()
    sig_packages_ready = Signal()
    sig_environment_created = Signal()
    sig_channels_updated = Signal(tuple, tuple)  # channels, active_channels

    def __init__(self,
                 parent=None,
                 name=None,
                 prefix=None,
                 channels=(),
                 active_channels=(),
                 conda_url='https://conda.anaconda.org'):

        super(CondaPackagesDialog, self).__init__(
            parent=parent,
            name=name,
            prefix=prefix,
            channels=channels,
            active_channels=active_channels,
            conda_url=conda_url,
            )

        # Widgets setup
        self.button_ok.setVisible(True)
        self.button_ok.setMaximumSize(self.button_ok.sizeHint())

        # Signals
        self.button_ok.clicked.connect(self.accept)

    def reject(self):
        """ """
        if self.busy:
            answer = QMessageBox.question(
                self,
                'Quit Conda Manager?',
                'Conda is still busy.\n\nDo you want to quit?',
                buttons=QMessageBox.Yes | QMessageBox.No)

            if answer == QMessageBox.Yes:
                QDialog.reject(self)
                # Do some cleanup?
        else:
            QDialog.reject(self)


# TODO:  update packages.ini file
# TODO: Define some automatic tests that can include the following:

# Test 1
# Find out if all the urls in the packages.ini file lead to a webpage
# or if they produce a 404 error

# Test 2
# Test installation of custom packages

# Test 3
# nothing is loaded on the package listing but clicking on it will produce an
# nonetype error


def test_widget():
    """Run conda packages widget test"""
    from conda_manager.utils.qthelpers import qapplication
    app = qapplication()
    widget = CondaPackagesWidget(None, prefix='/home/goanpeca/anaconda2')
    widget.show()
    sys.exit(app.exec_())


def test_dialog():
    """Run conda packages widget test"""
    from conda_manager.utils.qthelpers import qapplication
    app = qapplication()
    dialog = CondaPackagesDialog(name='root')
    dialog.exec_()
    sys.exit(app.exec_())


if __name__ == '__main__':
    test_dialog()
    #test_widget()
