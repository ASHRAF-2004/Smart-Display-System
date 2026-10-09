"""Cheap public smoke checks for auth configuration and the empty TV grid fix."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'.tools/final-acceptance/vendor'),str(ROOT/'.tools/upgrade/vendor')]
import requests
from playwright.sync_api import sync_playwright
URL='https://thebekal.com'
expected='464713909773-3doehhabv5j9s52uafedcmu93fq8o4td.apps.googleusercontent.com'
assert requests.get(URL+'/',timeout=15).status_code==200
assert requests.get(URL+'/api/state',timeout=15).status_code==200
assert requests.post(URL+'/api/tasks',json={},headers={'Origin':URL},timeout=15).status_code==401
assert requests.get(URL+'/api/auth/session',timeout=15).json()['clientId']==expected
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome Dev\Application\chrome.exe',headless=True)
    page=browser.new_page(viewport={'width':1920,'height':1080})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(URL);page.wait_for_function("expected=>window.staffAuth?.clientId===expected",arg=expected)
    page.wait_for_selector('.tv-card')
    result=page.evaluate("""()=>{const b=document.getElementById('tv-tasks-container'),c=b.querySelector('.tv-card');return {cols:b.style.getPropertyValue('--tv-cols'),box:b.getBoundingClientRect().toJSON(),card:c.getBoundingClientRect().toJSON(),role:window.staffAuth.role}}""")
    assert result['role']=='viewer'
    if page.locator('#tv-task-count').inner_text().startswith('0 active'):
        assert result['cols']=='1' and result['card']['width']>=result['box']['width']-2,result
    assert not errors,errors
    page.screenshot(path=str(ROOT/'.logs/upgrade-production/public-empty-tv-fixed.png'))
    browser.close()
print('CONFIG_AND_EMPTY_TV_SMOKE_PASS')
