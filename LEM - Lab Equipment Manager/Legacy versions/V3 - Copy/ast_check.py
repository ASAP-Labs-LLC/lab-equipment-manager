import ast,sys
ok=True
for f in ['app.py','main_window.py','data_source.py','models.py','dialogs.py']:
    try:
        print('Parsing', f)
        ast.parse(open(f,'r',encoding='utf-8-sig').read(), filename=f)
    except Exception as e:
        ok=False
        print('ERR in', f, type(e).__name__, e)
print('OK' if ok else 'FAIL')
