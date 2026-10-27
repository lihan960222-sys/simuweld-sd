import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WeldTimeline } from '../frontend/weldTimeline.ts';

test('按照路径长度计时，经过转角、焊后停止和下一条焊路',()=>{
  const timeline=new WeldTimeline([
    {name:'折线',points:[[0,0,0],[3,0,0],[3,4,0]],pause_after:2,heat_source:'Laser-A',velocity:2},
    {name:'返回',points:[[30,0,0],[20,0,0]],pause_after:1,heat_source:'Laser-B',velocity:5},
  ]);
  assert.equal(timeline.duration,8.5);
  assert.deepEqual(timeline.at(1).point,[2,0,0]);
  assert.equal(timeline.at(1).path_index,0);
  assert.equal(timeline.at(1).segment_index,0);
  assert.deepEqual(timeline.at(2.5).point,[3,2,0]);
  assert.equal(timeline.at(2.5).segment_index,1);
  assert.equal(timeline.at(4).welding,false);
  assert.deepEqual(timeline.at(4).point,[3,4,0]);
  assert.equal(timeline.at(5.5).name,'返回');
  assert.deepEqual(timeline.at(6).point,[27.5,0,0]);
  assert.equal(timeline.at(6).heat_source,'Laser-B');
  assert.equal(timeline.at(6).path_index,1);
  assert.equal(timeline.at(8.5).welding,false);
  assert.deepEqual(timeline.at(8.5).point,[20,0,0]);
  assert.equal(timeline.at(1).segment_index,0);
  assert.throws(()=>new WeldTimeline([]));
  assert.throws(()=>new WeldTimeline([{name:'a',points:[[0,0,0],[0,0,0]],pause_after:0,heat_source:'Laser-A',velocity:2}]));
  assert.throws(()=>new WeldTimeline([{name:'a',points:[[0,0,0],[1,0,0]],pause_after:0,heat_source:'Laser-A',velocity:0}]));
});
