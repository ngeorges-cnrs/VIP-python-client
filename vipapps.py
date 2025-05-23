from vip_client.utils import vip
import os
import sys
import json
import boutiques
import contextlib
import urllib.parse
import copy
import re
from pathlib import Path

# global flags
init_api_done = False
getapps_with_oldapi = False
debug = False
silent = False

# print message on stderr
def printerr(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)

# print message on stderr and exit
def fatal_error(*args, **kwargs) -> None:
    printerr(*args, **kwargs)
    exit(1)

# get VIP server URL
def get_vip_url() -> str:
    if not "VIP_API_URL" in os.environ:
        fatal_error("VIP_API_URL not set")
    return os.environ["VIP_API_URL"]

# initialize VIP API
def init_api() -> None:
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

# get_apps: new version based on GET /rest/admin/applications
def get_apps_newapi() -> list:
    def convert_app_version(av):
        name = av["applicationName"]
        identifier = name+"/"+av["version"]
        desc = json.loads(av["descriptor"])
        return {"name":name,"identifier":identifier,"descriptor":desc,"rawtext":av["descriptor"]}
    init_api()
    app_versions = vip.generic_get("admin/appVersions")
    return list(map(convert_app_version, app_versions))

# get_apps: older version based on GET /rest/pipeline. Unused, but preserving compatibility for now just in case. Watch out, apps list may be incomplete in some conditions (e.g. apps without groups). This does 1+N requests.
def get_apps_oldapi() -> list:
    init_api()
    pipelines = vip.list_pipeline()
    apps = []
    for pipeline in pipelines:
        name = pipeline.get("name")
        identifier = pipeline.get("identifier")
        # get descriptor
        desc = vip.get_descriptor(identifier)
        apps.append({"name":name,"identifier":identifier,"descriptor":desc})
    return apps

# get_apps(): get a list of apps and descriptors from a VIP-portal instance
def get_apps() -> list:
    if getapps_with_oldapi:
        return get_apps_oldapi()
    else:
        return get_apps_newapi()

# a picky checker on the "container-image" section of descriptors:
# . avoid useless values for "index"
# . it checks that image names have the form "host.domain/path/repository:tag"
#   where the host/path part is typically "docker.io/library" for dockerhub,
#   a URL to some other registry.
# . a tag must be present and not "latest"
def check_container_image(filepath: str, contimg: dict) -> None:
    # container-image.index
    if "index" in contimg and contimg["index"] != "docker://":
        printerr("warning: %s: suspicious index '%s'" % (filepath, contimg["index"]))
    # container-image.image
    image = contimg["image"]
    # :tag part
    if ":" not in image:
        printerr("warning: %s: image name has no tag" % filepath)
    else:
        img_tag = image.split(":")
        if len(img_tag) != 2:
            printerr("warning: %s: wrong number of ':'" % filepath)
        elif img_tag[1] == "latest":
            printerr("warning: %s: tag 'latest' shouldn't be used" % filepath)
    # host/path items
    items = image.split("/")
    if len(items) < 2 or "." not in items[0]:
        printerr("warning: %s: image name lacks a host part" % filepath) # "docker.io" on dockerhub
    elif len(items) < 3:
        printerr("warning: %s: image name lacks an explicit path" % filepath) # typically "library/" on dockerhub

# load a descriptor from a file, and check its validity
def load_descriptor(filepath) -> dict:
    try:
        # just check that we can open the file, to get some cleaner exception
        # than what boutiques.validate() raises on file not found
        with open(filepath, "r") as f:
            pass
        # bosh validate
        # https://boutiques.github.io/doc/_validate.html#python-api
        with open("/dev/null", "w") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f): # silence boutiques output
            boutiques.validate(str(filepath))
    # boutiques exception is over-verbose when checking a json file that
    # doesn't match its schema: catch the relevant exception, and show a
    # shorter message.
    # Also note that ValueError is raised by our own checks,
    # and also a parent of simplejson.errors.JSONDecodeError in boutiques
    except (ValueError,boutiques.DescriptorValidationError) as e:
        if type(e) == boutiques.DescriptorValidationError:
            raise ValueError("bosh validate failed")
        else:
            raise e
    # parse the descriptor again, for our own use
    file = {"path":filepath, "identifier":"", "descriptor":None, "rawtext":None}
    with open(filepath, "r") as f:
        rawtext = f.read()
        f.seek(0)
        desc = json.load(f)
        appname = desc["name"]
        appversion = desc["tool-version"]
        # check name and version strings
        if not re.match(r"^[a-zA-Z0-9_\. +-]+$", appname):
            raise ValueError("invalid name '%s'" % appname)
        if not re.match(r"^[a-zA-Z0-9_\.@ +-]+$", appversion):
            raise ValueError("invalid version '%s'" % appversion)
        # check container-image (just warnings)
        if not silent:
            if not "container-image" in desc:
                printerr("warning: %s: no container-image" % filepath)
            else:
                check_container_image(filepath, desc["container-image"])
        # store a parsed version of the descriptor in "descriptor",
        # and the exact original file content in "rawtext"
        file["identifier"] = appname + "/" + appversion
        file["descriptor"] = desc
        file["rawtext"] = rawtext
    return file

# get an identifier-indexed dict of valid boutiques descriptors in a directory.
# This uses a flat list of *.json files, no recursive directories walk so far.
def get_descriptor_files(dirname: str) -> dict:
    local_path = Path(dirname)
    files = {}
    for f in local_path.iterdir():
        if f.is_file() and f.match("*.json"):
            filepath = local_path.joinpath(f)
            try:
                file = load_descriptor(filepath)
                identifier = file["identifier"]
                if identifier in files and not silent:
                    printerr("ignoring %s: duplicate identifier '%s'"
                             % (filepath, identifier))
                files[identifier] = file
            # skip invalid files
            except ValueError as e:
                if not silent:
                    printerr("ignoring %s: invalid descriptor (%s)" % (filepath, e))
                pass
    return files

# import an app from a descriptor file to a VIP-portal instance
# file is assumed already loaded and checked, VIP-portal will re-check anyways
def import_file(file, is_overwrite=False):
    init_api()
    # XXX context - these should be parameters. Also add "origin".
    user = "admin@example.com" # could be automatic server-side
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
    r = vip.generic_put("admin/applications/"+urllib.parse.quote(app["name"]), app)
    if debug:
        print("app updated:",r)
    r = vip.generic_put("admin/appVersions/"+urllib.parse.quote(app["name"])+"/"+urllib.parse.quote(appver["version"]), appver)
    if debug:
        print("appVersion updated:",r)

# recursive ordering of nested list/dict structures
# it transforms any dict into a list of 2-tuples to make lists of dicts sortable
def ordered(obj) -> object:
    if isinstance(obj, dict):
        return sorted((k, ordered(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return sorted(ordered(x) for x in obj)
    else:
        return obj

# delete an array key if present and empty
def pop_if_empty(desc, field) -> None:
    if field in desc and type(desc[field]) == list and len(desc[field]) == 0:
        desc.pop(field)

# descriptor "normalization"
# When VIP-portal serializes a parsed Descriptor, it adds empty arrays where
# there were null or missing keys. This is only useful if using an API that
# doesn't preserve the raw descriptor text.
def clean_descriptor(desc: dict) -> dict:
    desc = copy.deepcopy(desc)
    pop_if_empty(desc, "online-platform-urls")
    pop_if_empty(desc, "groups")
    # we don't pop "inputs" as it's mandatory in boutiques
    pop_if_empty(desc, "output-files")
    pop_if_empty(desc, "tests")
    pop_if_empty(desc, "error-codes")
    pop_if_empty(desc, "environment-variables")
    if "container-image" in desc:
        item = desc["container-image"]
        pop_if_empty(item, "container-opts")
    for item in desc["inputs"]:
        pop_if_empty(item, "requires-inputs")
        pop_if_empty(item, "disables-inputs")
        pop_if_empty(item, "value-choices")
    if "output-files" in desc:
        for item in desc["output-files"]:
            pop_if_empty(item, "conditional-path-template")
            pop_if_empty(item, "path-template-stripped-extensions")
            pop_if_empty(item, "file-template")
    return desc

# compare two descriptors
def compare_descriptors(d1, d2) -> bool:
    # use raw text when available (should always be the case with the admin API)
    if "rawtext" in d1 and "rawtext" in d2:
        return d1["rawtext"] == d2["rawtext"]
    # otherwise, normalize, and ignore keys order for comparison
    d1 = d1["descriptor"]
    d2 = d2["descriptor"]
    return ordered(clean_descriptor(d1))==ordered(clean_descriptor(d2))

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
    for identifier in descriptors:
        file = descriptors[identifier]
        print("%s: %s" % (file["path"].name, identifier))

# import a single descriptor file
def cmd_import_file(filepath: str):
    file = None
    try:
        file = load_descriptor(filepath)
    except ValueError as e:
        fatal_error("%s is not a valid descriptor: %s" % (filepath, e))
    import_file(file)

# sync apps from a directory of boutiques descriptors to a VIP instance
def cmd_sync(dirname: str, dry_run=True, show_orphans=False, show_unchanged=False):
    apps = get_apps()
    files = list(get_descriptor_files(dirname).values())
    # sort both lists, then do one linear pass on them
    # we could also use dicts and the sets of their keys.
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
            if compare_descriptors(app, file):
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
    # XXX file index (csv, with groups/resources) + normalized desc.name
    elif command == "list_dir":
        if len(sys.argv) < 3:
            fatal_error("usage: list_dir <dir>")
        cmd_list_dir(sys.argv[2])
    elif command == "import_file":
        if len(sys.argv) < 1:
            fatal_error("usage: import_file <file>")
        cmd_import_file(sys.argv[2])
    elif command == "check_file":
        if len(sys.argv) < 3:
            fatal_error("usage: check_file <file>")
        filepath = sys.argv[2]
        try:
            file = load_descriptor(filepath)
        except ValueError as e:
            fatal_error("%s is not a valid descriptor: %s" % (filepath, e))
        print("OK")
    elif command == "sync":
        if len(sys.argv) < 3:
            fatal_error("usage: sync <dir>")
        #cmd_sync(sys.argv[2], dry_run=True, show_orphans=True, show_unchanged=False)
        cmd_sync(sys.argv[2], dry_run=False, show_orphans=False, show_unchanged=False)
    # debug commands
    elif command == "show_apps":
        print(get_apps())
    elif command == "show_files":
        if len(sys.argv) < 3:
            fatal_error("usage: show_files <dir>")
        print(get_descriptor_files(sys.argv[2]))
    else:
        fatal_error("unknown command '%s'" % command)

### entry point
if __name__ == "__main__":
    main()
