import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WeldTimeline } from '../frontend/weldTimeline.ts';

test('按照路径长度计时，经过转角、焊后停止和下一条焊路',()=>{
  const timeline=new WeldTimeline([
    {name:'折线',points:[[0,0,0],[3,0,0],[3,4,0]],pause_after:2},
    {name:'返回',points:[[30,0,0],[20,0,0]],pause_after:1},
  ],2);
  assert.equal(timeline.duration,11.5);
  assert.deepEqual(timeline.at(1).point,[2,0,0]);
  assert.deepEqual(timeline.at(2.5).point,[3,2,0]);
  assert.equal(timeline.at(4).welding,false);
  assert.deepEqual(timeline.at(4).point,[3,4,0]);
  assert.equal(timeline.at(5.5).name,'返回');
  assert.deepEqual(timeline.at(6).point,[29,0,0]);
  assert.equal(timeline.at(11.5).welding,false);
  assert.deepEqual(timeline.at(11.5).point,[20,0,0]);
  assert.throws(()=>new WeldTimeline([],2));
  assert.throws(()=>new WeldTimeline([{name:'a',points:[[0,0,0],[0,0,0]],pause_after:0}],2));
  assert.throws(()=>new WeldTimeline([{name:'a',points:[[0,0,0],[1,0,0]],pause_after:0}],0));
});
