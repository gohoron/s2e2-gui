from __future__ import print_function

import json
import hashlib
import os

from django.shortcuts import render
from django.http import HttpResponseServerError, HttpResponse, HttpResponseBadRequest
from django.utils.encoding import smart_text

from configure_and_run_analysis.launch_s2e import launch_s2e, create_new_s2e_project
from configure_and_run_analysis import models, utils
from configure_and_run_analysis.models import S2ELaunchException
from configure_and_run_analysis.extract_basic_blocks import generate_graph
from display_all_analysis.models import Analysis
from s2e_web import S2E_settings
import learn_plugin.learn_plugin


LIST_TYPE = "list"
BOOLEAN_TYPE = "bool"
INT_TYPE = "int"
STRING_TYPE = "string"
STRING_LIST_TYPE = "stringList"
INT_LIST_TYPE = "intList"
ACCEPTED_TYPES = [BOOLEAN_TYPE, INT_TYPE, STRING_TYPE, STRING_LIST_TYPE, LIST_TYPE, INT_LIST_TYPE]

TYPE_KEY = "type"
DESCRIPTION_KEY = "description"
CONTENT_KEY = "content"

plugins = None
configure_plugins_html = None


def handleRequest(request):
    """
    Handle the request from the server
    """
    global plugins
    global configure_plugins_html

    if not plugins:
        if not os.path.isfile(S2E_settings.S2E_PLUGIN_JSON_CONFIG_FILE):
            learn_plugin.learn_plugin.generate_configuration_for_plugins()

        with open(S2E_settings.S2E_PLUGIN_JSON_CONFIG_FILE, "r") as jsonFile:
            plugins = json.load(jsonFile)

    if request.method == 'POST':
        if request.POST["method"] == "get_config":
            return handle_get_config_request(request, plugins)
        elif request.POST["method"] == "run_s2e":
            return handle_run_s2e_request(request, plugins)

        return HttpResponse(status=404)
    else:
        #TODO change to cache data
        #if(configure_plugins_html == None):
        configure_plugins_html = render(request, 'configure_plugins/index.html',
                                        {'plugins': plugins, 'pluginsJson': json.dumps(plugins)})

        return configure_plugins_html


def handle_get_config_request(request, plugins):
    try:
        selectedPluginsConfig = json.loads(request.POST["data"])
        selectedPlugins = getSelectedPlugins(selectedPluginsConfig)

        configFileContent = generateConfigFileString(selectedPlugins, selectedPluginsConfig, "your_binary_name")

        response = HttpResponse(configFileContent, content_type='text/plain')
        response['Content-Disposition'] = 'attachment; filename="s2e-config.lua"'

        return response

    except AttributeError as err:
        print(err)
        return HttpResponseServerError(err)
    except S2ELaunchException as err:
        print(err)
        return HttpResponseServerError(err)


def handle_run_s2e_request(request, plugins):
    try:
        timeout = int(request.POST["timeout"])
        if timeout <= 0:
            return HttpResponseBadRequest("The timeout cannot be negative or zero")

        project_name = request.POST["binary_name"]

        binary_path = os.path.join(S2E_settings.S2E_BINARY_FOLDER_PATH, project_name)
        project_path = os.path.join(S2E_settings.S2E_PROJECT_FOLDER_PATH, project_name)

        if not os.path.exists(S2E_settings.S2E_BINARY_FOLDER_PATH):
            os.makedirs(S2E_settings.S2E_BINARY_FOLDER_PATH)

        utils.write_file_to_disk_and_close(binary_path, request.FILES["binary_file"])

        create_error = 0
        if not os.path.isdir(project_path):
            create_error = create_new_s2e_project(binary_path)

        if create_error != 0:
            return HttpResponseBadRequest("Unable to create a project with the given binary")

        s2e_num = find_next_analysis_num(project_name)

        selectedPluginsConfig = json.loads(request.POST["data"])
        selectedPlugins = getSelectedPlugins(selectedPluginsConfig)

        configFileContent = generateConfigFileString(selectedPlugins, selectedPluginsConfig, project_name)
        utils.write_string_to_disk_and_close(os.path.join(S2E_settings.S2E_PROJECT_FOLDER_PATH, project_name,
                                                          "s2e-config.lua"), configFileContent)

        has_s2e_error, killed_by_timeout = launch_s2e(timeout, project_name)
        add_entry_to_database(s2e_num, project_name, binary_path)

        s2e_output_dir = os.path.join(S2E_settings.S2E_PROJECT_FOLDER_PATH, project_name, "s2e-out-%d" % s2e_num)

        models.generate_lcov_files(s2e_output_dir, project_name)
        function_paths = generate_graph(s2e_output_dir, s2e_num, project_name)

        custom_data = models.CustomAnalysisData(killed_by_timeout, has_s2e_error, function_paths)
        custom_data.save_to_disk(s2e_output_dir)

        return render_output(s2e_output_dir, custom_data.data, s2e_num, project_name, request)

    except AttributeError as err:
        print(err)
        return HttpResponseServerError(err)
    except S2ELaunchException as err:
        print(err)
        return HttpResponseServerError(err)


def displayAnalysisInDir(request, dir_num, binary_name):
    """
    Display the analysis from inside the directory number
    """
    s2e_output_dir = os.path.join(S2E_settings.S2E_PROJECT_FOLDER_PATH, binary_name, 's2e-out-%d' % dir_num)

    custom_data = models.CustomAnalysisData()
    custom_data.get_from_disk(s2e_output_dir)

    return render_output(s2e_output_dir, custom_data.data, dir_num, binary_name, request)


def getSelectedPlugins(request_data):
    """
    Gets all the plugin configurations from the request data
    """
    global plugins
    selectedPlugins = []

    for plugin in plugins:
        if plugin["name"] in request_data.keys():
            selectedPlugins.append(plugin)

    return selectedPlugins


def generateConfigFileString(selectedPlugins, selectedPluginsConfig, binary_name):
    """
    Write the config.lua file with the selected plugins and the user configuration.
    """
    # Generate the base s2e config
    configFileContent = "s2e = {\n"
    configFileContent += "\t kleeArgs = {}\n"
    configFileContent += "}\n\n"

    # Generate the plugin list
    configFileContent += "plugins = {\n"

    for plugin in selectedPlugins:
        configFileContent += '\t"%s",\n' % plugin['name']


    # add the HostFiles plugin, it is used to run the analysis
    configFileContent += '\t "HostFiles" ,\n'
    # add the Vmi plugin, it is used to run the analysis
    configFileContent += '\t "Vmi" ,\n'
    # add BaseInstructions to the list
    configFileContent += '\t "BaseInstructions" \n'
    configFileContent += "}\n\n"

    configFileContent += generate_plugins_configurations(selectedPlugins, selectedPluginsConfig)
    configFileContent += generate_HostFiles_config(binary_name)
    configFileContent += generate_Vmi_config(binary_name)
    configFileContent += add_linux_monitor_config()

    return configFileContent


def add_linux_monitor_config():
    """
    The linux monitor configuration.
    """
    out = "dofile('library.lua')\n"
    out += 'add_plugin("LinuxMonitor")\n'
    out += 'pluginsConfig.LinuxMonitor = {\n'
    out += '-- Kill the execution state when it encounters a segfault\n'
    out += '    terminateOnSegFault = true,\n'
    out += '-- Kill the execution state when it encounters a trap\n'
    out += 'terminateOnTrap = true,\n'
    out += '}\n'
    return out


def generate_HostFiles_config(binary_name):
    """
    The HostFiles configuration.
    """
    configContent = ""
    configContent += "pluginsConfig.HostFiles = {\n"
    configContent += "\t baseDirs = {\"%s\"},\n" % os.path.join(S2E_settings.S2E_PROJECT_FOLDER_PATH, binary_name)
    configContent += "\t allowWrite = true,\n"
    configContent += "}\n"

    return configContent


def generate_Vmi_config(binary_name):
    """
    The Vmi configuration
    """
    configContent = ""
    configContent += "pluginsConfig.Vmi = {\n"
    configContent += "\t baseDirs = {\"%s\"}\n" % os.path.join(S2E_settings.S2E_PROJECT_FOLDER_PATH, binary_name)
    configContent += "}\n"

    return configContent


def generate_plugins_configurations(selectedPlugins, selectedPluginsConfig):
    """
    Generate the plugin specific configuration for the selected plugins with the user data
    """
    configContent = ""
    configContent += "pluginsConfig = {}\n\n"

    for plugin in selectedPlugins:
        userConfigs = selectedPluginsConfig[plugin["name"]]

        if plugin["configOptions"]:
            configContent += "pluginsConfig.%s = {\n" % plugin["name"]
            configContent += translate_to_lua(plugin["configOption"], userConfigs)
            configContent += "}\n\n"

    return configContent


def translate_to_lua(configPattern, userConfigs):
    """
    Translates a plugin configuration and user configuration into a string for the config.lua file.
    """
    output = ""
    configLen = len(configPattern)

    for index, (configPatternKey, configPatternValue) in enumerate(configPattern.items()):
        userConfigValue = userConfigs[configPatternKey]
        check_user_config(userConfigValue, configPatternValue)
        configPatternType = configPatternValue[TYPE_KEY]

        if configPatternType == LIST_TYPE:
            listLen = len(userConfigValue)
            for listIndex, (key, value) in enumerate(userConfigValue.items()):
                if not key:
                    raise S2ELaunchException("the list keys cannot be empty")

                output += str(key) + "= {\n"
                output += translate_to_lua(configPatternValue["content"], value)
                output += "}"
                if listIndex != listLen - 1:
                    output += ","
                output += "\n"
        elif configPatternType == STRING_TYPE:
            output += str(configPatternKey) + "='" + str(userConfigValue) + "'"
        elif configPatternType == STRING_LIST_TYPE or configPatternType == INT_LIST_TYPE:
            listLen = len(userConfigValue)
            output += str(configPatternKey) + " = {"
            for listIndex, listElem in enumerate(userConfigValue):
                if configPatternType == STRING_LIST_TYPE:
                    output += "'"
                output += listElem
                if configPatternType == STRING_LIST_TYPE:
                    output += "'"
                if listIndex != listLen - 1:
                    output += ", "
            output += "}"
        else:
            output += str(configPatternKey) + "=" + str(userConfigValue)


        if index != configLen - 1:
            output += ","

        output += "\n"

    return output


def check_user_config(userValue, expectedValue):
    """
    Checks if the user entered valid configuration data.
    """
    expectedType = expectedValue[TYPE_KEY]
    if expectedType == BOOLEAN_TYPE:
        if userValue != "true" and userValue != "false":
            raise S2ELaunchException("expected boolean but was: %s" % userValue)
    elif expectedType == INT_TYPE:
        if not is_integer(userValue):
            raise S2ELaunchException("expected integer but was: %s" % userValue)
    elif expectedType == LIST_TYPE:
        pass
    elif expectedType == STRING_TYPE:
        pass
    elif expectedType == INT_LIST_TYPE:
        if not isinstance(userValue, list):
            raise S2ELaunchException("expected a list of integers but was: %s" % userValue)
        for userValueInt in userValue:
            if not is_integer(userValueInt):
                raise S2ELaunchException("expected integer but was: %s" % userValueInt)

    elif expectedType == STRING_LIST_TYPE:
        if not isinstance(userValue, list):
            raise S2ELaunchException("expected a list of string but was: %s" % userValue)
    else:
        raise S2ELaunchException("plugins configuration are incorrect, unknown type: %s" % expectedType)


def is_integer(s):
    """
    Check if a string is an integer
    """
    try:
        int(s)
        return True
    except ValueError:
        return False


def render_output(s2e_output_dir, custom_data, s2e_num, project_name, request):
    """
    Render an html file for the analysis in the output directory with the given data.
    """
    output = models.S2EOutput(s2e_output_dir)
    stats = models.generate_stats(s2e_output_dir)
    has_coverage, line_coverage_path = models.get_lcov_path(s2e_output_dir, s2e_num, project_name)
    icount = models.generate_icount_files(s2e_output_dir)

    print(icount)

    html_data_dictionary = {'warnings': smart_text(output.warnings, encoding="utf-8", errors="ignore"),
                            'info': smart_text(output.info, encoding="utf-8", errors="ignore"),
                            'debug': smart_text(output.debug, encoding="utf-8", errors="ignore"),
                            'line_coverage_exist': has_coverage,
                            'line_coverage_report_path': line_coverage_path,
                            'custom_data': custom_data}

    #The html_page contains the content in the header, hence the 39 first characters must be removed
    html_page = str(render(request, 'display_log/index.html', html_data_dictionary))

    return HttpResponse(json.dumps({"stats": smart_text(stats, encoding="utf-8", errors="ignore"),
                                    "html":  html_page[39:],
                                    "icount": icount}))



def find_next_analysis_num(binary_name):
    """
    Finds the next analysis number from the binary project folder
    """
    s2e_num = -1
    result_dirs = os.listdir(os.path.join(S2E_settings.S2E_PROJECT_FOLDER_PATH, binary_name))
    analysis_numbers = [int(name[8:]) for name in result_dirs if name.startswith("s2e-out")]
    i = 0
    while s2e_num == -1:
        if i not in analysis_numbers:
            s2e_num = i
        i += 1

    return s2e_num

def add_entry_to_database(s2e_num, project_name, binary_path):
    """
    Adds an entry to the Analysis database.
    """
    cksum = hash_bytestr_iter(file_as_blockiter(open(binary_path, 'rb')), hashlib.sha256())

    a = Analysis(s2e_num=s2e_num, binary_checksum=cksum, binary_name=project_name)
    a.save()

def hash_bytestr_iter(bytesiter, hasher):
    """
    Hash the block iterator with the given hasher.
    """
    for block in bytesiter:
        hasher.update(block)
    return hasher.hexdigest()

def file_as_blockiter(afile, blocksize=65536):
    """
    Gets te block iterator of a file
    """
    with afile:
        block = afile.read(blocksize)
        while block:
            yield block
            block = afile.read(blocksize)
