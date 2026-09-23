import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter,Route,Routes,useLocation,useParams} from 'react-router-dom';
import VideoLineage,{LineageBack,useSegmentPreview} from '../src/components/VideoLineage';
import {videoLibraryApi} from '../src/api/client';
import type {VideoLineageAsset,VideoLineageResult} from '../src/types';
import {aggregateReports,demoReport,money} from '../src/components/videoPerformanceDemo';

vi.mock('../src/api/client',()=>({mediaUrl:(value:string)=>value,videoLibraryApi:{lineage:vi.fn()}}));
const source:VideoLineageAsset={id:1,asset_id:'source',filename:'source.mp4',asset_kind:'final_output',library_type:'finished',object_url:'/source.mp4',duration_seconds:20,reference_count:7,created_at:'2026-09-17T00:00:00Z'};
const target:VideoLineageAsset={...source,id:2,asset_id:'target',filename:'target.mp4',object_url:'/target.mp4',reference_count:0};
const result:VideoLineageResult={source_count:1,reference_count:7,total:1,page:1,page_size:6,items:[{source,target,created_at:'2026-09-17T00:00:00Z',segments:[
  {id:1,source_start:5,source_end:9,target_start:2,target_end:6},
  {id:2,source_start:12,source_end:15,target_start:10,target_end:13},
  {id:3,source_start:16,source_end:18,target_start:17,target_end:19},
]}]};
const api=vi.mocked(videoLibraryApi.lineage);

beforeEach(()=>{
  api.mockReset();api.mockResolvedValue(result);
  vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{});
  vi.spyOn(HTMLMediaElement.prototype,'play').mockResolvedValue();
  HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','')};
  HTMLDialogElement.prototype.close=function(){this.removeAttribute('open')};
  HTMLElement.prototype.scrollIntoView=vi.fn();
  window.scrollTo=vi.fn();
});
afterEach(cleanup);

function mount(path='/final-videos/1',asset=source,onLocate=vi.fn()){
  return render(<MemoryRouter initialEntries={[path]}><VideoLineage asset={asset} onLocate={onLocate} analytics={<p>投放示例</p>}/></MemoryRouter>);
}

describe('视频溯源交互',()=>{
  it('汇总所有筛选成片, 消耗日期同步影响卡片和片段表现',async()=>{
    api.mockResolvedValue({...result,total:8,matched_asset_ids:[2,3,4,5,6,7,8,9]});
    mount('/final-videos/1?tab=used');
    await screen.findByRole('button',{name:'查看引用片段'});
    const summary=document.querySelector('.summary-scroll')!;
    expect(summary.textContent).toContain(money(aggregateReports([2,3,4,5,6,7,8,9],'2026-09-11','2026-09-17').spend));
    fireEvent.change(screen.getByLabelText('消耗起始日期'),{target:{value:'2026-09-17'}});
    expect(summary.textContent).toContain(money(aggregateReports([2,3,4,5,6,7,8,9],'2026-09-17','2026-09-17').spend));
    fireEvent.click(screen.getByRole('button',{name:'查看引用片段'}));
    const drawer=screen.getByRole('dialog');
    expect(drawer.textContent).toContain('当前片段的时长分摊估算');
    expect(drawer.textContent).toContain(money(demoReport(2,'2026-09-17','2026-09-17').spend));
    expect(drawer.textContent).toContain('20.00%');
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('时间轴保留空白, 抽屉切换片段后定位到成片区间',async()=>{
    const locate=vi.fn();mount('/final-videos/2',target,locate);
    const marker=await screen.findByRole('button',{name:'source.mp4, 00:02.000 - 00:06.000'});
    expect(marker.style.left).toBe('10%');expect(marker.style.width).toBe('20%');
    fireEvent.click(marker);
    expect(screen.getByRole('dialog').getAttribute('open')).toBe('');
    fireEvent.click(screen.getByRole('button',{name:'片段 2'}));
    const players=screen.getByRole('dialog').querySelectorAll('video');
    fireEvent.loadedMetadata(players[0]);fireEvent.loadedMetadata(players[1]);
    expect(players[0].currentTime).toBe(12);expect(players[1].currentTime).toBe(10);
    fireEvent.click(screen.getByRole('button',{name:'定位到当前视频片段'}));
    expect(locate).toHaveBeenCalledWith(10,13);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('引用成片和片段数量分开展示, 反向定位使用原片时间',async()=>{
    const locate=vi.fn();mount('/final-videos/1?tab=used',source,locate);
    await screen.findByText('3 个引用片段');
    expect(screen.getByRole('tab',{name:'被引用后出片 7'}).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByText('引用次数 0')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'查看引用片段'}));
    fireEvent.click(screen.getByRole('button',{name:'定位到当前视频片段'}));
    expect(locate).toHaveBeenCalledWith(5,9);
  });

  it('错误日期不阻止切回镜头溯源, 素材不出现多余标签',async()=>{
    const view=mount('/final-videos/1?tab=used&lineage_start=2026-09-18&lineage_end=2026-09-17');
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(api).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab',{name:'镜头溯源'}));
    await screen.findAllByRole('button',{name:'对照片段'});
    view.unmount();
    mount('/semi-finished-assets/1',{...source,asset_kind:'material',library_type:'semi_finished'});
    expect(screen.getAllByRole('tab')).toHaveLength(1);
    await screen.findByRole('button',{name:'查看引用片段'});
  });

  it('关联详情返回后保留筛选, 分页和点击时的滚动位置',async()=>{
    api.mockResolvedValue({...result,total:8,page:2});
    Object.defineProperty(window,'scrollY',{configurable:true,value:360});
    function Page(){
      const {id}=useParams();const location=useLocation();
      return <><LineageBack fallback="/final-videos" label="返回成片库"/><output>{location.pathname+location.search}</output><VideoLineage key={id} asset={id==='1'?source:target} onLocate={()=>{}}/></>;
    }
    render(<MemoryRouter initialEntries={['/final-videos/1?tab=used&lineage_q=target&lineage_page=2']}><Routes><Route path="/final-videos/:id" element={<Page/>}/></Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole('link',{name:'target.mp4',exact:true}));
    fireEvent.click(screen.getByRole('tab',{name:/被引用后出片/}));
    fireEvent.change(screen.getByLabelText('搜索成片'),{target:{value:'child filter'}});
    fireEvent.click(await screen.findByRole('link',{name:'返回上一条视频'}));
    await screen.findByRole('link',{name:'target.mp4',exact:true});
    expect(screen.getByRole('status').textContent).toBe('/final-videos/1?tab=used&lineage_q=target&lineage_page=2');
    expect(screen.getByLabelText('搜索成片')).toHaveProperty('value','target');
    await waitFor(()=>expect(window.scrollTo).toHaveBeenCalledWith(0,360));
  });

  it('加载失败允许重试, 未知历史来源显示空状态',async()=>{
    api.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({...result,total:0,source_count:0,items:[]});
    mount();
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button',{name:'重试'}));
    await screen.findByText('暂无来源记录');
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('主预览定位后在片段末尾暂停, 用户跳出片段后可自由播放',()=>{
    function Preview(){const p=useSegmentPreview(1);return <><video data-testid="preview" ref={p.videoRef} onTimeUpdate={p.onTimeUpdate} onSeeking={p.onSeeking}/><button onClick={()=>p.locate(2,6)}>定位</button></>}
    render(<Preview/>);
    const video=screen.getByTestId('preview') as HTMLVideoElement;
    Object.defineProperty(video,'readyState',{configurable:true,value:1});
    fireEvent.click(screen.getByRole('button',{name:'定位'}));
    expect(video.currentTime).toBe(2);
    vi.mocked(video.pause).mockClear();video.currentTime=6;fireEvent.timeUpdate(video);
    expect(video.pause).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button',{name:'定位'}));
    vi.mocked(video.pause).mockClear();video.currentTime=12;fireEvent.seeking(video);fireEvent.timeUpdate(video);
    expect(video.pause).not.toHaveBeenCalled();
  });
});
