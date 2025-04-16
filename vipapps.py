from vip_client.utils import vip
import os

def main():
  vip.set_vip_url(os.environ["VIP_API_URL"])
  vip.setApiKey(os.environ["VIP_API_KEY"])
  #print(vip.platform_info())
  apps = vip.list_pipeline()
  for app in apps:
    print("--- ",app.get("name")+" "+app.get("identifier"),":")
    #print(app)
    print(vip.get_descriptor(app.get("identifier")))

###
main()
