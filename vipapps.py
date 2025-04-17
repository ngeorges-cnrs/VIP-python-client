from vip_client.utils import vip
import os
import sys
import json
import boutiques
import contextlib
from pathlib import Path

# print message on stderr and exit
def fatal_error(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)
    exit(1)

# initialize VIP API
def init_api():
    # get VIP server URL and API key
    if not "VIP_API_URL" in os.environ:
        fatal_error("VIP_API_URL not set")
    vip_url = os.environ["VIP_API_URL"]
    if not "VIP_API_KEY" in os.environ:
        fatal_error("VIP_API_KEY not set")
    vip_apikey = os.environ["VIP_API_KEY"]
    vip.set_vip_url(vip_url)
    vip.setApiKey(vip_apikey)

# get apps and descriptors on a VIP instance
def get_apps() -> list:
    init_api()
    # get apps
    apps = vip.list_pipeline()
    # add descriptor
    for app in apps:
        app["descriptor"] = vip.get_descriptor(app.get("identifier"))
    return apps

# get boutiques descriptors in a directory
def get_descriptors(dirname: str) -> list:
    local_path = Path(dirname)
    files = []
    for f in local_path.iterdir():
        if f.is_file() and f.match("*.json"):
            filepath = local_path.joinpath(f)
            with open("/dev/null", "w") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f): # silence boutiques output
                try:
                    # https://boutiques.github.io/doc/_validate.html#python-api
                    boutiques.validate(str(filepath))
                    file = {"path":filepath, "descriptor":None, "identifier":""}
                    with open(filepath) as f:
                        desc = json.load(f)
                        file["descriptor"] = desc
                        file["identifier"] = desc["name"] + "/" + desc["tool-version"]
                    files.append(file)
                except ValueError: # parent of simplejson.errors.JSONDecodeError
                    pass # not json
                except boutiques.DescriptorValidationError:
                    pass # not a descriptor
    return files

# list apps and descriptors on a VIP instance
def list_apps():
    apps = get_apps()
    print("found %d apps on %s:" % (len(apps), os.environ["VIP_API_URL"]))
    for app in apps:
        print("%s: %s" % (app["name"], app["identifier"]))

# list apps from a directory of boutiques descriptors
def list_files(dirname: str):
    descriptors = get_descriptors(dirname)
    print("found %d descriptors in %s:" % (len(descriptors), dirname))
    for d in descriptors:
        print("%s: %s" % (d["path"].name, d["identifier"]))

# nested ordering of a list/dict structure
def ordered(obj):
    if isinstance(obj, dict):
        return sorted((k, ordered(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return sorted(ordered(x) for x in obj)
    else:
        return obj

# compare two descriptors
def check_descriptors(d1, d2) -> bool:
    # VIP adds some fields, remove them:
    # (XXX should be done on both sides, or directly in VIP)
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
    equal = ordered(d1)==ordered(d2)
    if not equal:
        #print(ordered(d1))
        #print(ordered(d2))
        #print(json.dumps(d1))
        #print(json.dumps(d2))
        pass
    return equal

# sync apps from a directory of boutiques descriptors to a VIP instance
def sync_apps(dirname: str):
    apps = get_apps()
    descriptors = get_descriptors(dirname)
    # sort both lists, then do one linear pass on them:
    apps.sort(key=lambda app: app["identifier"])
    descriptors.sort(key=lambda desc: desc["identifier"])
    napps = len(apps)
    ndescriptors = len(descriptors)
    i = 0
    j = 0
    while i < napps or j < ndescriptors:
        app = apps[i] if i < napps else None
        desc = descriptors[j] if j < ndescriptors else None
        if app != None and desc != None:
            if app["identifier"] < desc["identifier"]:
                desc = None
            elif app["identifier"] > desc["identifier"]:
                app = None
        if app != None and desc != None:
            # app identifiers match: check descriptors (?)
            if check_descriptors(app["descriptor"], desc["descriptor"]):
                print("%s: unchanged" % app["identifier"])
            else:
                print("%s: descriptor changed" % app["identifier"])
                # TODO: import with overwrite
            i += 1
            j += 1
        elif app != None:
            print("%s: only in apps" % app["identifier"])
            i += 1
        elif desc != None:
            print("%s: only in descriptors" % desc["identifier"])
            j += 1
            # TODO: import

### main
def main():
    # XXX TODO proper options/positional parsing
    if len(sys.argv) < 2:
        fatal_error("usage: vipapps <command>")
    command = sys.argv[1]
    if command == "list_apps":
        list_apps()
    elif command == "list_files":
        if len(sys.argv) < 3:
            fatal_error("usage: list_files <dir>")
        list_files(sys.argv[2])
    elif command == "sync_apps":
        if len(sys.argv) < 3:
            fatal_error("usage: sync_apps <dir>")
        sync_apps(sys.argv[2])
    else:
        fatal_error("unknown command '%s'" % command)

###
if __name__ == "__main__":
    main()
