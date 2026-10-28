import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.branding import VENDORS, vendor_info
from gateway.catalog import ModelCatalogError, load_model_catalog, authorized_catalog
from gateway.store import Store


def test_vendor_marks_follow_existing_and_new_model_registrations(tmp_path):
    assert [vendor_info(m)['vendor'] for m in ['glm5.3-flash','qwen3.8-flash-next','qwen3-32b','h3']] == ['zai','qwen','qwen','minimax']
    path = tmp_path/'catalog.json'
    path.write_text(json.dumps({'models':[{'id':'new-lab-model','display_name':'New lab model','kind':'text','deployment_status':'planned','vendor':'deepseek'}]}))
    rows = load_model_catalog(path)
    assert rows[0]['vendor_logo'] == '/model-logos/deepseek.svg'
    who = {'user':{'role':'caller','models':'["new-lab-model"]'},'key':{'models':'["new-lab-model"]'}}
    assert authorized_catalog(who,rows)[0]['vendor'] == 'deepseek'
    assert vendor_info('unidentified-model')['vendor'] == 'generic'
    document = json.loads(path.read_text()); document['models'][0]['vendor']='https://example.invalid/remote.svg'
    path.write_text(json.dumps(document))
    with pytest.raises(ModelCatalogError):load_model_catalog(path)


def test_bundled_vendor_svgs_are_local_static_and_not_dynamic_files(tmp_path):
    app = create_app(Store(tmp_path/'gateway.sqlite'), background=False)
    with TestClient(app) as client:
        for vendor in VENDORS:
            response = client.get('/model-logos/'+vendor+'.svg')
            assert response.status_code == 200
            assert response.headers['content-type'].startswith('image/svg+xml')
            root = ET.fromstring(response.content)
            for node in root.iter():
                assert node.tag.rsplit('}',1)[-1] not in ('script','foreignObject','image','use')
                assert all(not key.lower().startswith('on') for key in node.attrib)
        assert client.get('/model-logos/unregistered.svg').status_code == 404
