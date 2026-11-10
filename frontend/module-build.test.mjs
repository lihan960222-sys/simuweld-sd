import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'vite';

test('四个模块保留独立资源、稳定导出和共享依赖',async()=>{
  const result=await build({logLevel:'error',build:{write:false}});
  assert.ok(!Array.isArray(result));
  const output=result.output;
  const expected={
    face_load:['faceLoadPanel','faceLoadSelection','setupFaceLoad'],
    weld_paths:['WeldingTool'],
    thermal_cycle:['ThermalTool'],
    local_trial:['LocalTrialTool'],
  };
  for(const [id,exports] of Object.entries(expected)) {
    const base=`modules/${id}/${id}`;
    const chunk=output.find(item=>item.fileName===base+'.js');
    assert.equal(chunk?.type,'chunk');
    assert.deepEqual(chunk.exports.toSorted(),exports.toSorted());
    assert.ok(chunk.imports.length>0);
    for(const dependency of [...chunk.imports,...chunk.dynamicImports])assert.ok(dependency.startsWith('shared/'),`${id} 引用了 ${dependency}`);
    assert.deepEqual([...chunk.viteMetadata.importedCss],[base+'.css']);
    assert.ok(output.some(item=>item.type==='asset'&&item.fileName===base+'.css'));
  }
  const common=output.find(item=>item.fileName==='shared/common.js');
  assert.equal(common?.type,'chunk');
  for(const id of Object.keys(common.modules))assert.ok(!/\/(faceLoad|welding|heatSettings|weldTimeline|thermal|localTrial)\.ts$/.test(id));
  for(const item of output) {
    if(item.fileName.endsWith('.js')||item.fileName.endsWith('.css'))assert.match(item.fileName,/^(shared\/(index|common|vendor)|modules\/(face_load|weld_paths|thermal_cycle|local_trial)\/\3)\.(js|css)$/);
  }
});
