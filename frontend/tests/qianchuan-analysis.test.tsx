import {afterEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import QianchuanAnalysis from '../src/components/QianchuanAnalysis';
import type {QianchuanUpload} from '../src/api/client';

vi.mock('../src/components/QianchuanReportPanel',()=>({default:({upload}:{upload?:QianchuanUpload})=><div data-testid="report">报表素材：{upload?.material_id||'无'}</div>}));
vi.mock('../src/components/QianchuanUploadHistory',()=>({
  uploadStatus:(row?:QianchuanUpload)=>row?.status||'尚未上传素材',
  default:({uploads}:{uploads:QianchuanUpload[]})=><div data-testid="history">{uploads.map(row=>row.id).join(',')}</div>,
}));

const base:QianchuanUpload={
  id:'a-old',video_asset_id:78,binding_id:'binding-a',advertiser_id:'100',advertiser_name:'账号甲',
  filename:'test.mp4',is_aigc:false,status:'succeeded',phase:'',material_id:'material-a',video_id:'',
  error_message:'',created_at:'2026-09-20T00:00:00Z',updated_at:'2026-09-20T00:00:00Z',
  auto_pending:false,can_retry:false,can_verify:false,
};

afterEach(cleanup);

describe('千川投放分析账号切换',()=>{
  it('按账号同步切换状态、报表素材和上传记录',()=>{
    const onManage=vi.fn();
    const uploads=[
      {...base,id:'b-new',binding_id:'binding-b',advertiser_id:'200',advertiser_name:'账号乙',
        status:'failed',material_id:'',created_at:'2026-09-22T00:00:00Z'},
      {...base,id:'a-new',status:'uploaded',material_id:'',created_at:'2026-09-21T00:00:00Z'},
      base,
    ];
    render(<QianchuanAnalysis uploads={uploads} error="" reload={vi.fn()} onManage={onManage}/>);
    fireEvent.click(screen.getByRole('button',{name:'绑定或管理我的千川账号'}));
    expect(onManage).toHaveBeenCalledOnce();
    const selector=screen.getByRole('combobox',{name:'投放账号'}) as HTMLSelectElement;
    expect(selector.value).toBe('200');
    expect(screen.getByText('素材推送状态：failed')).toBeTruthy();
    expect(screen.getByTestId('report').textContent).toContain('无');
    expect(screen.getByTestId('history').textContent).toBe('b-new');

    fireEvent.change(selector,{target:{value:'100'}});
    expect(screen.getByText('素材推送状态：uploaded')).toBeTruthy();
    expect(screen.getByTestId('report').textContent).toContain('material-a');
    expect(screen.getByTestId('history').textContent).toBe('a-new,a-old');
  });
});
