from vip_client.utils import vip
import os,sys

def fatal_error(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)
    exit(1)

def init_api():
    # get VIP server URL and API key
    if not "VIP_API_URL" in os.environ:
        fatal_error("VIP_API_URL not set")
    vip_url = os.environ["VIP_API_URL"]
    if not "VIP_API_KEY" in os.environ:
        fatal_error("VIP_API_KEY not set")
    vip_apikey = os.environ["VIP_API_KEY"]
    # initialize VIP API
    vip.set_vip_url(vip_url)
    vip.setApiKey(vip_apikey)

def list_apps():
    init_api()
    #print(vip.platform_info())
    apps = vip.list_pipeline()
    for app in apps:
        print("--- ",app.get("name")+" "+app.get("identifier"),":")
        #print(app)
        print(vip.get_descriptor(app.get("identifier")))

def main():
    if len(sys.argv) < 2:
        fatal_error("usage: vipapps <command>")
    command = sys.argv[1]
    if command == "list_apps":
        list_apps()
    else:
        fatal_error("unknown command '%s'" % command)


###
if __name__ == "__main__":
    main()
