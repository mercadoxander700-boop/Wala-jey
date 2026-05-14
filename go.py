import requests, random, urllib3, time, webbrowser

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

fp={'fontsHash':'a1b2c3d4e5f6g7h8','canvasHash':'1234567890','canvasAndFontsHash':'x9y8z7w6v5u4t3s2','os':'win','osSpec':'win11'}
ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
ixcy_solver="http://localhost:3000/xa1/turnstile"
ixcynigga_api="https://api.gologin.com"

def gen_str(n=8):
    return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789',k=n))

def solve_captcha():
    print("=> solving nigga...")
    try:
        resp=requests.get(ixcy_solver,params={"hostname":"https://app.gologin.com/sign_up","sitekey":"0x4AAAAAAAQn-wN8S1gi-nJa"},headers={"User-Agent":ua},timeout=30)
        if resp.status_code!=200:return None
        tk=resp.json().get('token')
        if not tk:return None
        print(f"=> nigga solved: {tk[:20]}...")
        return tk
    except:
        print("=> nigga fail")
        return None

def get_prox(tk):
    print("=> fetching proxies...")
    hdr={'accept':'*/*','authorization':f'Bearer {tk}','gologin-meta-header':f'site-{fp["os"]}-10.0','user-agent':ua}
    try:
        resp=requests.get(f'{ixcynigga_api}/proxy/v2?page=1',headers=hdr,timeout=30,verify=False)
        if resp.status_code!=200:return False
        prox_list=resp.json().get('proxies',[])
        if not prox_list:return False
        with open('proxies.txt','a') as f:
            for p in prox_list:
                if all([p.get(x) for x in['username','password','host','port']]):
                    f.write(f"{p['username']}:{p['password']}:{p['host']}:{p['port']}\n")
        print(f"=> saved {len(prox_list)} proxies")
        return True
    except:
        print("=> nigga error")
        return False

def create_acc(tk):
    print("=> creating account...")
    email=f"user_{gen_str()}@ixcyon.top"
    pwd=f"tg@ixcynigga{random.randint(1000,9999)}"
    hdr={'accept':'*/*','accept-language':'en-US,en;q=0.9','content-type':'application/json','gologin-meta-header':f"site-{fp['os']}-10.0",'origin':'https://app.gologin.com','referer':'https://app.gologin.com/','user-agent':ua}
    body={'email':email,'password':pwd,'passwordConfirm':pwd,'captchaToken':tk,'fromApp':False,'canvasAndFontsHash':fp['canvasAndFontsHash'],'fontsHash':fp['fontsHash'],'canvasHash':fp['canvasHash'],'userOs':fp['os'],'osSpec':fp['osSpec'],'resolution':'1920x1080'}
    try:
        resp=requests.post(f'{ixcynigga_api}/user',params={'free-plan':'true','registerAs':'workspaces'},headers=hdr,json=body,timeout=30,verify=False)
        if resp.status_code in[200,201]:
            print(f"=> account created: {email}")
            tk2=resp.json().get('token')
            with open('accounts.txt','a') as f:f.write(f"{email}:{pwd}\n")
            print("=> saved to accounts.txt")
            time.sleep(0.5)
            get_prox(tk2)
            return True
        else:
            print(f"=> failed [{resp.status_code}]")
            return False
    except:
        print("=> nigga error")
        return False

def main():
    webbrowser.open("https://t.me/+-27YAufBqg0xNTQ9")
    webbrowser.open("https://t.me/ixcynigga")
    print("=> mf join https://t.me/+-27YAufBqg0xNTQ9")
    tk=solve_captcha()
    if not tk:return
    print("=> next nigga")
    if create_acc(tk):
        print("=> done")
    else:print("=> fail")

if __name__=="__main__":main()
