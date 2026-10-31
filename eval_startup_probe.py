"""Exercise actual train/eval Pipeline initialization; no API/network or training."""
import argparse,importlib.util,inspect,json,sys,tempfile
from pathlib import Path
from common import make_config,write

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--topology',required=True)
    a=p.parse_args();root=Path(a.root).resolve();sys.path[:0]=[str(root),str(root/'core')]
    spec=importlib.util.spec_from_file_location('v7_probe_pipeline',str(root/'run.py'))
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    # Main and __init__ are real. Only the runtime threads/HTTP loop are suppressed.
    instances=[]
    def inspect_only(self):
        instances.append(self)
        self.writer.close();self.file.close()
    mod.Pipeline.run=inspect_only
    original_load=mod.torch.load
    # Reproduce pre-weights_only signature even when the probe host has newer torch.
    def legacy_load(path,map_location=None,**kwargs):
        if 'weights_only' in kwargs:raise TypeError('weights_only rejected by legacy loader')
        options={'map_location':map_location}
        if 'weights_only' in inspect.signature(original_load).parameters:options['weights_only']=False
        return original_load(path,**options)
    with tempfile.TemporaryDirectory() as folder:
        folder=Path(folder);cfg=folder/'config.json';write(cfg,make_config(a.topology))
        sys.argv=['run.py','train-http','--config',str(cfg),'--output',str(folder/'smoke'),'--feedback-budget','30']
        mod.main();checkpoint=folder/'smoke/initial.pt'
        for label,loader in [('native',original_load),('legacy',legacy_load)]:
            mod.torch.load=loader
            sys.argv=['run.py','eval-http','--config',str(cfg),'--output',str(folder/label),'--checkpoint',str(checkpoint)]
            mod.main()
            manifest=json.loads((folder/label/'manifest.json').read_text())
            assert manifest['arguments']['mode']=='eval-http'
            for before,after in zip(instances[0].latest,instances[-1].latest):
                assert all(mod.torch.equal(v,after.state_dict()[k]) for k,v in before.state_dict().items())
        mod.torch.load=original_load
    print('PASS actual train initialization -> checkpoint -> eval manifest; native and legacy torch.load; identical actors')

if __name__=='__main__':main()
