import json,shutil,py_compile,step3
shutil.copy('make_live_config.py','make_live_config.py.bak_step3_20261103')
shutil.copy('config_live.json','config_live.json.after_rescale_20261103')
s=open('make_live_config.py',encoding='utf-8').read();a="'branch_display_name'):"
assert s.count(a)==1
open('make_live_config.py','w',encoding='utf-8').write(s.replace(a,"'branch_display_name','step3'):"))
py_compile.compile('make_live_config.py',doraise=True)
c=json.load(open('config_live.json',encoding='utf-8'))
c['step3']=dict(step3.DEF,note='Vien 03/11/2026 t631u: BUOC 3 hai chieu khi buoc 2 (loc 0.7%) chan tin hieu cung chieu')
json.dump(c,open('config_live.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
print('ok',json.load(open('config_live.json',encoding='utf-8'))['step3']['enabled'])
