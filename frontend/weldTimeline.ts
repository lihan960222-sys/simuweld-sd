export interface TimedPath { name: string; points: number[][]; pause_after: number; velocity: number; heat_source: string; }

export class WeldTimeline {
  readonly duration: number;
  private paths: {path: TimedPath; distances: number[]; start: number; weldEnd: number; end: number}[]=[];
  constructor(paths: TimedPath[]) {
    if(!paths.length) throw new Error('请添加焊路。');
    let time=0;
    for(const path of paths) {
      if(!Number.isFinite(path.velocity) || path.velocity<=0) throw new Error('焊路热源的速度必须大于零。');
      if(path.points.length<2 || !Number.isFinite(path.pause_after) || path.pause_after<0) throw new Error('焊路点数或停止时间无效。');
      const distances=[0];
      path.points.forEach((point,index)=>{
        if(point.length!==3 || !point.every(Number.isFinite)) throw new Error('路径坐标无效。');
        if(!index) return;
        const distance=Math.hypot(...point.map((value,axis)=>value-path.points[index-1][axis]));
        if(!Number.isFinite(distance) || distance<=0) throw new Error('相邻路径点距离必须大于零。');
        distances.push(distances[index-1]+distance);
      });
      const weldEnd=time+distances.at(-1)!/path.velocity, end=weldEnd+path.pause_after;
      if(!Number.isFinite(end) || end<=time) throw new Error('焊接时间超出可表示范围。');
      this.paths.push({path,distances,start:time,weldEnd,end});time=end;
    }
    this.duration=time;
  }

  at(time: number) {
    if(!Number.isFinite(time)) throw new Error('播放时间无效。');
    const elapsed=Math.min(this.duration,Math.max(0,time));
    const item=this.paths.find(item=>elapsed<item.end) || this.paths.at(-1)!;
    const {path,distances,start,weldEnd}=item;
    const distance=Math.min(distances.at(-1)!,Math.max(0,elapsed-start)*path.velocity);
    let lower=0,upper=distances.length-1;
    while(upper-lower>1) {const middle=(lower+upper)>>1;if(distances[middle]<=distance) lower=middle;else upper=middle;}
    const fraction=(distance-distances[lower])/(distances[upper]-distances[lower]);
    const point=path.points[lower].map((value,axis)=>value+(path.points[upper][axis]-value)*fraction);
    return {name:path.name,point,heat_source:path.heat_source,welding:elapsed<weldEnd && elapsed<this.duration};
  }
}
