from vip_client.utils import vip
import os
import sys
import json
import boutiques
import contextlib
from pathlib import Path

# global flags
init_api_done = False
getapps_with_oldapi = False
debug = False

# print message on stderr and exit
def fatal_error(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)
    exit(1)

# get VIP server URL
def get_vip_url():
    if not "VIP_API_URL" in os.environ:
        fatal_error("VIP_API_URL not set")
    return os.environ["VIP_API_URL"]

# initialize VIP API
def init_api():
    global init_api_done
    if init_api_done:
        return
    # get API key
    if not "VIP_API_KEY" in os.environ:
        fatal_error("VIP_API_KEY not set")
    vip_apikey = os.environ["VIP_API_KEY"]
    # configure both, in the right order
    vip.set_vip_url(get_vip_url())
    vip.setApiKey(vip_apikey)
    init_api_done = True

# get_apps(): get list of apps and descriptors from a VIP-portal instance
def get_apps() -> list:
    if getapps_with_oldapi:
        return get_apps_newapi()
    else:
        return get_apps_oldapi()

# new version based on GET /rest/admin/applications
def convert_app_version(av):
    name = av["applicationName"]
    identifier = name+"/"+av["version"]
    desc = json.loads(av["descriptor"])
    desc = ordered(desc)
    return {"name":name,"identifier":identifier,"descriptor":desc}
def get_apps_newapi() -> list:
    init_api()
    app_versions = vip.generic_get("admin/appVersions")
    return list(map(convert_app_version, app_versions))

# older version based on GET /rest/pipeline. Unused, but preserving compatibility for now just in case. Watch out, apps list may be incomplete in some conditions (e.g. apps without groups). This does 1+N requests.
def get_apps_oldapi() -> list:
    init_api()
    pipelines = vip.list_pipeline()
    apps = []
    for pipeline in pipelines:
        name = pipeline.get("name")
        identifier = pipeline.get("identifier")
        # get descriptor
        desc = vip.get_descriptor(identifier)
        clean_descriptor(desc)
        desc = ordered(desc)
        apps.append({"name":name,"identifier":identifier,"descriptor":desc})
    return apps

# nested ordering of a list/dict structure
def ordered(obj):
    if isinstance(obj, dict):
        return sorted((k, ordered(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return sorted(ordered(x) for x in obj)
    else:
        return obj

# descriptor normalization XXX should only delete keys if present&empty
def clean_descriptor(d1):
    if not "online-platform-urls" in d1:
        return
    d1.pop("online-platform-urls")
    d1.pop("groups")
    d1.pop("tests")
    d1.pop("error-codes")
    d1.pop("environment-variables")
    if "container-image" in d1:
        d1["container-image"].pop("container-opts")
    for item in d1["inputs"]:
        item.pop("requires-inputs")
        item.pop("disables-inputs")
        item.pop("value-choices")
    for item in d1["output-files"]:
        item.pop("conditional-path-template")
        item.pop("path-template-stripped-extensions")
        item.pop("file-template")

# compare two descriptors - XXX decide when we order/clean, might also need some diff
def compare_descriptors(d1, d2) -> bool:
    equal = ordered(d1)==ordered(d2)
    if not equal:
        print("---")
        print(ordered(d1))
        print(ordered(d2))
        print("---")
        pass
    return equal

# load a descriptor from a file, and check its validity
def load_descriptor(filepath) -> object:
    # https://boutiques.github.io/doc/_validate.html#python-api
    with open("/dev/null", "w") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f): # silence boutiques output
        boutiques.validate(str(filepath))
    # XXX todo check name/version/container-image (warnings)
    file = {"path":filepath, "identifier":"", "descriptor":None, "raw":None}
    with open(filepath) as f:
        rawtext = f.read()
        f.seek(0)
        desc = json.load(f)
        # XXX clean / order, or preserve original ?: both!
        file["identifier"] = desc["name"] + "/" + desc["tool-version"]
        file["descriptor"] = desc
        file["rawtext"] = rawtext
    return file

# get a list of valid boutiques descriptor files in a directory
# this uses a flat list of *.json files, no recursive directories walk so far
def get_descriptor_files(dirname: str) -> list:
    local_path = Path(dirname)
    files = []
    for f in local_path.iterdir():
        if f.is_file() and f.match("*.json"):
            filepath = local_path.joinpath(f)
            try:
                files.append(load_descriptor(filepath))
            # silently skip invalid files
            except ValueError: # parent of simplejson.errors.JSONDecodeError
                pass # not json
            except boutiques.DescriptorValidationError:
                pass # not a descriptor
    return files

# import an app from a descriptor file to a VIP-portal instance
# file is assumed already loaded and checked, VIP-portal will re-check anyways
def import_file(file, is_overwrite=False):
    init_api()
    # XXX context - these should be parameters
    user = "admin@example.com"
    groups = ["g2"]
    resources = ["r2"]
    tags = []
    settings = {}
    is_visible = True
    # create app and appVersion objects for the /rest/admin API
    appname = file["descriptor"]["name"]
    version = file["descriptor"]["tool-version"]
    descriptor = file["rawtext"]
    app = {"name":appname,"applicationGroups":groups,"owner":user}
    appver = {"applicationName":appname,"version":version,"descriptor":descriptor,"visible":is_visible,"resources":resources,"tags":tags,"settings":settings}
    msg = ""
    if is_overwrite:
        msg = " (overwrite)"
    print("importing app %s %s%s" % (appname,version,msg))
    if debug:
        print("descriptor string:", descriptor)
    r = vip.generic_put("admin/applications/"+app["name"], app)
    if debug:
        print("app updated:",r)
    r = vip.generic_put("admin/appVersions/"+app["name"]+"/"+appver["version"], appver)
    if debug:
        print("appVersion updated:",r)

# list apps and descriptors on a VIP instance
def cmd_list_apps():
    apps = get_apps()
    print("found %d apps on %s:" % (len(apps), get_vip_url()))
    for app in apps:
        print("%s: %s" % (app["name"], app["identifier"]))

# list apps from a directory of boutiques descriptors
def cmd_list_dir(dirname: str):
    descriptors = get_descriptor_files(dirname)
    print("found %d valid descriptors in %s:" % (len(descriptors), dirname))
    for d in descriptors:
        print("%s: %s" % (d["path"].name, d["identifier"]))

# import a single descriptor file
def cmd_import_file(filepath: str):
    file = None
    try:
        file = load_descriptor(filepath)
    # boutiques exception is over-verbose when checking a json file that
    # doesn't match its schema:
    # catch the relevant exception, and show a shorter message.
    except boutiques.DescriptorValidationError:
        fatal_error("%s is not a valid descriptor: %s" % (filepath, "bosh validate failed"))
    # other common errors (not json, file not found...) are short enough
    except Exception as e:
        fatal_error("%s is not a valid descriptor: %s" % (filepath, e))
    import_file(file)


# sync apps from a directory of boutiques descriptors to a VIP instance
def cmd_sync(dirname: str, dry_run=True, show_orphans=False, show_unchanged=False):
    apps = get_apps()
    files = get_descriptor_files(dirname)
    # sort both lists, then do one linear pass on them:
    apps.sort(key=lambda app: app["identifier"])
    files.sort(key=lambda file: file["identifier"])
    napps = len(apps)
    nfiles = len(files)
    i = 0
    j = 0
    while i < napps or j < nfiles:
        app = apps[i] if i < napps else None
        file = files[j] if j < nfiles else None
        # if we're not at the end of either list, check if app and file
        # identifiers match:
        # . if they don't, process the first one in sort order, and move on
        # . if they do, compare their descriptors
        if app != None and file != None:
            if app["identifier"] < file["identifier"]:
                file = None
            elif app["identifier"] > file["identifier"]:
                app = None
        if app != None and file != None:
            # app identifiers match: compare the descriptors
            if compare_descriptors(app["descriptor"], file["descriptor"]):
                if show_unchanged:
                    print("%s: unchanged" % app["identifier"])
            else:
                print("%s: descriptor changed" % app["identifier"])
                if not dry_run: # import, with overwrite
                    import_file(file, is_overwrite=True)
            i += 1
            j += 1
        elif app != None:
            if show_orphans:
                print("%s: only in apps" % app["identifier"])
            i += 1
        elif file != None:
            print("%s: only in files" % file["identifier"])
            if not dry_run: # import new app
                import_file(file, is_overwrite=False)
            j += 1

### main
def main():
    # XXX TODO proper options/positional parsing, import argparse...
    if len(sys.argv) < 2:
        fatal_error("usage: vipapps <command>\n"
                    "  <command>: list_apps, list_dir, import_file, sync")
    command = sys.argv[1]
    if command == "list_apps":
        cmd_list_apps()
    # XXX file index or recursive walk
    elif command == "list_dir":
        if len(sys.argv) < 3:
            fatal_error("usage: list_dir <dir>")
        cmd_list_dir(sys.argv[2])
    elif command == "import_file":
        if len(sys.argv) < 1:
            fatal_error("usage: import_file <file>")
        cmd_import_file(sys.argv[2])
    elif command == "sync":
        if len(sys.argv) < 3:
            fatal_error("usage: sync <dir>")
        #cmd_sync(sys.argv[2], dry_run=True, show_orphans=True, show_unchanged=False)
        cmd_sync(sys.argv[2], dry_run=False, show_orphans=True, show_unchanged=False)
    # debug commands
    elif command == "show_apps":
        print(get_apps())
    elif command == "show_files":
        if len(sys.argv) < 3:
            fatal_error("usage: show_files <dir>")
        print(get_descriptor_files(sys.argv[2]))
    else:
        fatal_error("unknown command '%s'" % command)

###
if __name__ == "__main__":
    main()
