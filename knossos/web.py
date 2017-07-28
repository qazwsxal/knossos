## Copyright 2017 Knossos authors, see NOTICE file
##
## Licensed under the Apache License, Version 2.0 (the "License");
## you may not use this file except in compliance with the License.
## You may obtain a copy of the License at
##
##     http://www.apache.org/licenses/LICENSE-2.0
##
## Unless required by applicable law or agreed to in writing, software
## distributed under the License is distributed on an "AS IS" BASIS,
## WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
## See the License for the specific language governing permissions and
## limitations under the License.

from __future__ import absolute_import, print_function

import sys
import os.path
import logging
import re
import json
import stat
import semantic_version

from .qt import read_file, QtCore, QtGui, QtWidgets, QtWebChannel
from . import center, runner, repo, windows, tasks, util, settings

if not QtWebChannel:
    from .qt import QtWebKit


class WebBridge(QtCore.QObject):
    _path = None

    showWelcome = QtCore.Signal()
    showDetailsPage = QtCore.Signal('QVariant')
    showRetailPrompt = QtCore.Signal()
    updateModlist = QtCore.Signal(str, str)
    modProgress = QtCore.Signal(str, float, str)
    settingsArrived = QtCore.Signal(str)

    taskStarted = QtCore.Signal(float, str, list)
    taskProgress = QtCore.Signal(float, float, str)
    taskFinished = QtCore.Signal(float)

    def __init__(self, webView=None):
        super(WebBridge, self).__init__()

        if QtWebChannel:
            self.bridge = self
            page = webView.page()
            channel = QtWebChannel.QWebChannel(page)

            page.setWebChannel(channel)
            channel.registerObject('fs2mod', self)

            if center.DEBUG and os.path.isdir('../html') and os.environ.get('KN_BABEL') != 'True':
                link = os.path.abspath('../html/index_debug.html')
                if sys.platform == 'win32':
                    link = '/' + link.replace('\\', '/')

                link = 'file://' + link
                self._path = os.path.abspath('../html')
            else:
                link = 'qrc:///html/index.html'
                self._path = ':/html'

            webView.load(QtCore.QUrl(link))
        else:
            self._path = ':/html'

    @QtCore.Slot(str, result=str)
    def loadTemplate(self, name):
        path = os.path.join(self._path, 'templates', os.path.basename(name) + '.html')

        try:
            if path.startswith(':/'):
                data = read_file(path)
                if data:
                    return data
                else:
                    raise Exception('Qt failed to read "%s"!' % path)
            else:
                with open(path, 'r') as stream:
                    return stream.read()
        except Exception:
            logging.exception('Failed to load template %s!' % name)

            if not center.DEBUG:
                # These messages can get annoying. Don't display them in DEBUG mode since then they'd show up in the log.
                QtWidgets.QMessageBox.critical(None, 'Knossos', self.tr('Failed to load template "%s". The UI might be broken.') % name)

            return ''

    @QtCore.Slot('QVariantList', result='QVariantMap')
    def finishInit(self, tr_keys):
        trs = {}
        for k in tr_keys:
            trs[k] = QtCore.QCoreApplication.translate('modlist_ts', k)

        center.main_win.finish_init()
        return trs

    @QtCore.Slot(result=str)
    def getVersion(self):
        return center.VERSION

    @QtCore.Slot(result='QVariantList')
    def getMods(self):
        return list(center.mods.get())

    @QtCore.Slot(result='QVariantList')
    def getInstalledMods(self):
        return list(center.installed.get())

    @QtCore.Slot(result='QVariantMap')
    def getUpdates(self):
        updates = center.installed.get_updates()
        result = {}
        for mid, items in updates.items():
            versions = result[mid] = {}
            for ver_a, ver_b in items.items():
                versions[str(ver_a)] = str(ver_b)

        return result

    @QtCore.Slot(str, str, result=bool)
    def isInstalled(self, mid, spec=None):
        if spec is None:
            return mid in center.installed.mods
        else:
            spec = util.Spec(spec)
            mod = center.installed.mods.get(mid, None)
            if mod is None:
                return False

            return spec.match(mod.version)

    @QtCore.Slot(str, str, result='QVariantMap')
    def query(self, mid, spec=None):
        if spec is not None:
            if spec == '':
                spec = None
            else:
                if re.search(r'^\d+', spec):
                    spec = '==' + spec

                try:
                    spec = util.Spec(spec)
                except Exception:
                    logging.exception('Invalid spec "%s" passed to query()!', spec)
                    return -2

        try:
            return center.mods.query(mid, spec).get()
        except Exception:
            return None

    @QtCore.Slot()
    def fetchModlist(self):
        tasks.run_task(tasks.FetchTask())

    @QtCore.Slot(bool, result='QVariantList')
    def requestModlist(self, async=False):
        if async:
            center.main_win.update_mod_list()
            return [None]
        else:
            return list(center.main_win.search_mods())

    @QtCore.Slot(str)
    def showTab(self, name):
        try:
            center.main_win.update_mod_buttons(name)
        except Exception:
            logging.exception('Failed to switch tabs!')

    @QtCore.Slot(str)
    def triggerSearch(self, term):
        center.main_win.perform_search(term)

    def _get_mod(self, mid, spec=None, mod_repo=None):
        if spec is not None:
            if spec == '':
                spec = None
            else:
                if re.search(r'^\d+', spec):
                    spec = '==' + spec

                try:
                    spec = util.Spec(spec)
                except Exception:
                    logging.exception('Invalid spec "%s" passed to a web API function!', spec)
                    return -2

        if mod_repo is None:
            mod_repo = center.installed

        try:
            return mod_repo.query(mid, spec)
        except repo.ModNotFound:
            logging.exception('Couldn\'t find mod "%s" (%s)!', mid, spec)
            return -1

    @QtCore.Slot(str, str, result=int)
    def showAvailableDetails(self, mid, spec=None):
        mod = self._get_mod(mid, spec, center.mods)
        if mod in (-1, -2):
            return mod

        self.showDetailsPage.emit(mod.get())
        return 0

    @QtCore.Slot(str, str, result=int)
    def showInstalledDetails(self, mid, spec=None):
        mod = self._get_mod(mid, spec)
        if mod in (-1, -2):
            return mod

        self.showDetailsPage.emit(mod.get())
        return 0

    @QtCore.Slot(str, str, 'QStringList', result=int)
    def install(self, mid, spec=None, pkgs=None):
        mod = self._get_mod(mid, spec, center.mods)
        if mod in (-1, -2):
            logging.debug('fs2mod.install(%s, %s) = %d', mid, spec, mod)
            return mod

        if pkgs is None:
            pkgs = []

        if mod.parent == 'FS2':
            has_retail = False
            if center.settings['base_path'] is not None:
                fs2_path = os.path.join(center.settings['base_path'], 'FS2')
                
                if os.path.isdir(fs2_path):
                    for item in os.listdir(fs2_path):
                        if item.lower() == 'root_fs2.vp':
                            has_retail = True
                            break

            if not has_retail:
                self.showRetailPrompt.emit()
                return 0
        
        windows.ModInstallWindow(mod, pkgs)
        return 0

    @QtCore.Slot(str, str, 'QStringList', result=int)
    def uninstall(self, mid, spec=None, pkgs=None):
        mod = self._get_mod(mid, spec)
        if mod in (-1, -2):
            return mod

        if len(pkgs) == 0:
            plist = mod.packages
        else:
            plist = []
            pfound = set()
            for pkg in mod.packages:
                if pkg.name in pkgs:
                    plist.append(pkg)
                    pfound.add(pkg.name)

            if len(pfound) < len(pkgs):
                # Some packages are missing
                pmissing = set(pkgs) - pfound
                logging.warning('Missing packages %s.', ', '.join(pmissing))
                return -2

        titles = [pkg.name for pkg in plist if center.installed.is_installed(pkg)]
        # FIXME: Check if any other mod dependes on this mod before uninstalling it to avoid broken dependencies.

        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Question)
        msg.setText(self.tr('Do you really want to uninstall %s?') % (mod.title,))

        if len(titles) > 0:
            msg.setInformativeText(self.tr('%s will be removed.') % (', '.join(titles)))

        msg.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        msg.setDefaultButton(QtWidgets.QMessageBox.No)

        if msg.exec_() == QtWidgets.QMessageBox.Yes:
            tasks.run_task(tasks.UninstallTask(plist, mods=[mod]))
            return True
        else:
            return False

    @QtCore.Slot(str, str, result=int)
    def updateMod(self, mid, spec=None):
        mod = self._get_mod(mid, spec)

        if mod in (-1, -2):
            return mod

        all_vers = list(center.installed.query_all(mid))
        if len(all_vers) == 1:
            # Only one version is installed, let's update it.
            tasks.run_task(tasks.UpdateTask(mod))
        else:
            # Just install the new version
            cur_pkgs = list(mod.packages)
            for i, pkg in enumerate(cur_pkgs):
                cur_pkgs[i] = center.mods.query(mod.mid, None, pkg.name)

            tasks.run_task(tasks.InstallTask(cur_pkgs, cur_pkgs[0].get_mod()))

        center.main_win.update_mod_buttons('progress')
        return 0

    @QtCore.Slot(float)
    def abortTask(self, tid):
        if hasattr(center.main_win, 'abort_task'):
            center.main_win.abort_task(int(tid))

    @QtCore.Slot(str, str, result=int)
    def runMod(self, mid, spec=None):
        mod = self._get_mod(mid, spec)
        if mod in (-1, -2):
            return mod

        runner.run_mod(mod)
        return 0

    @QtCore.Slot(str, str, result=int)
    def runFredMod(self, mid, spec=None):
        mod = self._get_mod(mid, spec)
        if mod in (-1, -2):
            return mod

        runner.run_mod(mod, fred=True)
        return 0

    @QtCore.Slot(str, str, result=int)
    def vercmp(self, a, b):
        try:
            a = semantic_version.Version(a)
            b = semantic_version.Version(b)
        except Exception:
            # logging.exception('Someone passed an invalid version to vercmp()!')
            return 0

        return a.__cmp__(b)

    @QtCore.Slot(str)
    def openExternal(self, link):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(link))

    @QtCore.Slot(str, str, result=str)
    def browseFolder(self, title, path):
        return QtWidgets.QFileDialog.getExistingDirectory(None, title, path)

    @QtCore.Slot(str, str, str, result=list)
    def browseFiles(self, title, path, filter_):
        res = QtWidgets.QFileDialog.getOpenFileNames(None, title, path, filter_)
        if res:
            return res[0]
        else:
            return []

    @QtCore.Slot(str)
    def setBasePath(self, path):
        if not os.path.isdir(path):
            QtWidgets.QMessageBox.critical(None, 'Knossos', self.tr('The selected path is not a directory!'))
        else:
            center.settings['base_path'] = os.path.abspath(path)
            center.save_settings()
            tasks.run_task(tasks.FetchTask())
            center.main_win.check_fso()

    @QtCore.Slot()
    def getSettings(self):
        def cb(res):
            self.settingsArrived.emit(json.dumps(res))

        settings.get_settings(cb)

    @QtCore.Slot(str, str)
    def saveSetting(self, key, value):
        try:
            value = json.loads(value)
        except Exception:
            logging.exception('Failed to decode new value for setting "%s"! (%s)' % (key, value))
        else:
            settings.save_setting(key, value)

    @QtCore.Slot(str)
    def saveFsoSettings(self, data):
        try:
            data = json.loads(data)
        except Exception:
            logging.exception('Failed to decode new FSO settings! (%s)' % data)
        else:
            settings.save_fso_settings(data)

    @QtCore.Slot(result=str)
    def getDefaultFsoCaps(self):
        flags = None

        if center.settings['fs2_bin']:
            try:
                flags = settings.get_fso_flags(center.settings['fs2_bin'])

                if flags:
                    flags = flags.to_dict()
            except Exception:
                logging.exception('Failed to fetch FSO flags!')

        try:
            return json.dumps(flags)
        except Exception:
            logging.exception('Failed to encode FSO flags!')

    @QtCore.Slot(result=str)
    def searchRetailData(self):
        # TODO: Add Steam path
        for path in [r'C:\GOG Games\Freespace2']:
            if os.path.isdir(path):
                return path

        return ''

    @QtCore.Slot(str, result=bool)
    def copyRetailData(self, path):
        if os.path.isdir(path):
            tasks.run_task(tasks.GOGCopyTask(path, os.path.join(center.settings['base_path'], 'FS2')))
            return True
        elif os.path.isfile(path) and path.endswith('.exe'):
            tasks.run_task(tasks.GOGExtractTask(path, os.path.join(center.settings['base_path'], 'FS2')))
            return True
        else:
            QtWidgets.QMessageBox.critical(None, 'Knossos', self.tr('The selected path is not a directory!'))
            return False

    @QtCore.Slot(result=str)
    def getRunningTasks(self):
        tasks = center.main_win.get_tasks()
        res = {}

        for t, task in tasks.items():
            res[t] = {
                'title': task.title,
                'mods': task.mods
            }

        try:
            return json.dumps(res)
        except Exception:
            logging.exception('Failed to encoding running tasks!')
            return 'null'

    @QtCore.Slot(str, str, str, str, str, result=bool)
    def createMod(self, name, mid, version, mtype, parent):
        if mtype in ('mod', 'ext'):
            if parent != 'FS2':
                parent = self._get_mod(parent)

                if parent == -1:
                    QtWidgets.QMessageBox.critical(None, 'Knossos', self.tr('The selected parent TC is not valid!'))
                    return False
                else:
                    parent = parent.mid
        else:
            parent = None

        mod = repo.InstalledMod({
            'title': name,
            'id': mid,
            'version': version,
            'type': mtype,
            'parent': parent
        })
        mod.generate_folder()

        if os.path.isdir(mod.folder):
            # TODO: Check online, too?
            QtWidgets.QMessageBox.critical(None, 'Knossos', self.tr('There already exists a mod with the chosen ID!'))
            return False

        upper_folder = os.path.dirname(mod.folder)
        if not os.path.isdir(upper_folder):
            if mod.mtype in ('tool', 'engine') and upper_folder.endswith('bin'):
                try:
                    os.mkdir(upper_folder)
                except Exception:
                    logging.exception('Failed to create binary folder! (%s)' % upper_folder)
                    QtWidgets.QMessageBox.critical(None, 'Knossos', self.tr('I could not create the folder for binaries!'))
                    return False
            else:
                logging.error('%s did not exist during mod creation! (parent = %s)' % (mod.folder, mod.parent))
                QtWidgets.QMessageBox.critical(None, 'Knossos', self.tr('The chosen parent does not exist! Something went very wrong here!!'))
                return False

        pkg = repo.InstalledPackage({
            'name': 'Content',
            'status': 'required',
            'folder': 'content'
        })

        if mtype in ('tool', 'engine'):
            pkg.folder = '.'

        os.mkdir(mod.folder)
        mod.add_pkg(pkg)
        mod.save()

        center.installed.add_mod(mod)
        center.main_win.update_mod_list()

        return True

    @QtCore.Slot(str, str, str, str, result=int)
    def addPackage(self, mid, version, pkg_name, pkg_folder):
        mod = self._get_mod(mid, version)
        if mod in (-1, -2):
            return mod

        pkg = mod.add_pkg(repo.Package({'name': pkg_name}))
        pkg.folder = pkg_folder

        pkg_path = os.path.join(mod.folder, pkg_folder)
        if not os.path.isdir(pkg_path):
            os.mkdir(pkg_path)

        mod.save()
        center.main_win.update_mod_list()

        return len(mod.packages) - 1

    @QtCore.Slot(str, str, int, result=bool)
    def deletePackage(self, mid, version, idx):
        mod = self._get_mod(mid, version)
        if mod in (-1, -2):
            return False

        if idx < 0 or idx >= len(mod.packages):
            logging.error('Invalid index passed to deletePackage()!')
            return False

        # TODO: Delete the package folder?
        del mod.packages[idx]
        mod.save()
        center.main_win.update_mod_list()

        return True

    @QtCore.Slot(str, result=str)
    def selectImage(self, old_path):
        if old_path == '':
            old_dir = None
        else:
            old_dir = os.path.dirname(old_path)

        new_path, used_filter = QtWidgets.QFileDialog.getOpenFileName(None, self.tr('Please select an image'), old_dir,
                                                                      self.tr('Image (*.png *.jpg *.jpeg *.gif *.bmp)'))

        if new_path:
            return new_path
        else:
            return old_path

    @QtCore.Slot(str, result=list)
    def addPkgExe(self, folder):
        if sys.platform == 'win32':
            filter_ = self.tr('Executables (*.exe)')
        else:
            filter_ = '*'

        res = QtWidgets.QFileDialog.getOpenFileNames(None, self.tr('Please select one or more executables'),
            folder, filter_)

        if not res:
            return []
        else:
            return [os.path.relpath(item, folder) for item in res[0]]

    @QtCore.Slot(str, result=list)
    def findPkgExes(self, folder):
        result = []

        for path, dirs, files in os.walk(folder):
            for fn in files:
                fn = os.path.join(path, fn)

                if sys.platform == 'win32':
                    if fn.endswith('.exe'):
                        result.append(fn)
                elif '.so' not in fn and os.stat(fn).st_mode & stat.S_IXUSR == stat.S_IXUSR:
                    result.append(fn)

        return [os.path.relpath(item, folder) for item in result]

    @QtCore.Slot(str)
    def saveModDetails(self, data):
        try:
            data = json.loads(data)
        except Exception:
            logging.exception('Failed to decode mod details!')
            QtWidgets.QMessageBox.critical(None, 'Error', self.tr('Internal data inconsistency. Please try again.'))
            return

        mod = self._get_mod(data['id'], data['version'])
        if mod == -1:
            logging.error('Failed find mod "%s" during save!' % data['id'])
            QtWidgets.QMessageBox.critical(None, 'Error', self.tr('Failed to find the mod! Weird...'))
            return

        mod.description = data['description']

        if data['logo_path'] != mod.logo_path:
            mod.logo_path = data['logo_path']
            mod.logo = os.path.relpath(mod.folder, data['logo_path'])

        if data['tile_path'] != mod.tile_path:
            mod.tile_path = data['tile_path']
            mod.tile = os.path.relpath(mod.folder, data['tile_path'])

        mod.release_thread = data['release_thread']
        mod.videos = []
        for line in data['video_urls'].split('\n'):
            line = line.strip()
            if line != '':
                mod.videos.append(line)

        mod.first_release = data['first_release']
        mod.last_update = data['last_update']

        mod.save()
        center.main_win.update_mod_list()

    @QtCore.Slot(str, str, str, str)
    def savePackage(self, mid, version, pkg_name, data):
        try:
            data = json.loads(data)
        except Exception:
            logging.exception('Failed to decode mod details!')
            QtWidgets.QMessageBox.critical(None, 'Error', self.tr('Internal data inconsistency. Please try again.'))
            return

        mod = self._get_mod(mid, version)
        if mod == -1:
            logging.error('Failed find mod "%s" during save!' % mid)
            QtWidgets.QMessageBox.critical(None, 'Error', self.tr('Failed to find the mod! Weird...'))
            return

        pkg = None
        for item in mod.packages:
            if item.name == pkg_name:
                pkg = item
                break

        if not pkg:
            logging.error('Failed to find package "%s" for mod "%s"!' % (pkg_name, mid))
            QtWidgets.QMessageBox.critical(None, 'Error', self.tr('Failed to find the package! Weird...'))
            return

        pkg.notes = data['notes']
        pkg.status = data['status']
        pkg.dependencies = data['dependencies']

        if mod.mtype in ('engine', 'tool'):
            pkg.environment = data['environment']
            pkg.executables = data['executables']
        else:
            pkg.environment = None
            pkg.executables = []

        mod.save()
        center.main_win.update_mod_list()

    @QtCore.Slot(str, str, str, str)
    def saveModFsoDetails(self, mid, version, build, cmdline):
        mod = self._get_mod(mid, version)
        if mod == -1:
            logging.error('Failed find mod "%s" during save!' % mid)
            QtWidgets.QMessageBox.critical(None, 'Error', self.tr('Failed to find the mod! Weird...'))
            return

        build = build.split('#')
        if len(build) != 2:
            logging.error('saveModFsoDetails(): build is not correctly formatted! (%s)' % build)
        else:
            try:
                exes = mod.get_executables()
            except repo.NoExecutablesFound:
                done = False
                for pkg in mod.packages:
                    if pkg.status == 'required':
                        pkg.dependencies.append({
                            'id': build[0],
                            'version': build[1]
                        })
                        done = True
                        break

                if not done:
                    QtWidgets.QMessageBox.critical(None, 'Error',
                        self.tr('Failed to save the selected FSO build. Make sure that you have at least one required' +
                        ' package!'))
            else:
                old_build = exes[0]['mod']
                done = False

                for pkg in mod.packages:
                    for dep in pkg.dependencies:
                        if dep['id'] == old_build.mid:
                            dep['id'] = build[0]
                            dep['version'] = '>=' + build[1]
                            done = True
                            break

                    if done:
                        break

                if not done:
                    logging.error('Failed to update build dependency for "%s"! WHY?!?! (old_build = %s, new_build = %s)'
                        % (mod, old_build, build[0]))

        mod.cmdline = cmdline
        mod.save()

        center.main_win.update_mod_list()

    @QtCore.Slot(str, str, result=str)
    def getFsoBuild(self, mid, version):
        mod = self._get_mod(mid, version)

        try:
            for item in mod.get_executables():
                if not item['debug']:
                    mod = item['mod']
                    return mod.mid + '#' + str(mod.version)
        except repo.NoExecutablesFound:
            return ''
        except Exception:
            logging.exception('Failed to fetch executables!')

        return ''

    @QtCore.Slot(str, str, result=str)
    def getFsoCaps(self, mid, version):
        flags = None
        mod = self._get_mod(mid, version)

        try:
            flags = settings.get_fso_flags(mod.get_executables()[0]['file'])

            if flags:
                flags = flags.to_dict()
        except repo.NoExecutablesFound:
            return 'null'
        except Exception:
            logging.exception('Failed to fetch FSO flags!')

        try:
            return json.dumps(flags)
        except Exception:
            logging.exception('Failed to encode FSO flags!')


if QtWebChannel:
    BrowserCtrl = WebBridge
else:
    class BrowserCtrl(object):
        _view = None
        _nam = None
        bridge = None

        def __init__(self, webView):
            self._view = webView
            self.bridge = WebBridge()

            settings = webView.settings()
            settings.setAttribute(QtWebKit.QWebSettings.DeveloperExtrasEnabled, True)

            frame = webView.page().mainFrame()
            frame.javaScriptWindowObjectCleared.connect(self.insert_bridge)

            link = 'qrc:///html/index.html'
            webView.load(QtCore.QUrl(link))

        def insert_bridge(self):
            frame = self._view.page().mainFrame()

            del self.bridge
            self.bridge = WebBridge()
            frame.addToJavaScriptWindowObject('fs2mod', self.bridge)
