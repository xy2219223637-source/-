import {afterEach,describe,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import QianchuanPlanCatalog from '../src/components/QianchuanPlanCatalog';

const api=vi.hoisted(()=>({
  planCapabilities:vi.fn(),accounts:vi.fn(),awemes:vi.fn(),products:vi.fn(),
}));

vi.mock('../src/api/client',()=>({qianchuanApi:api}));
vi.mock('../src/components/QianchuanUploadModal',()=>({
  qianchuanError:()=> '目录请求失败',
}));

const gate={
  enabled:true,write_enabled:false,worker_enabled:false,binding_id:null,
  credential_version:null,can_create:false,capabilities:[],
};
const accounts={
  configuration:{status:'ready',missing:[]},can_manage:true,
  connections:[{id:'connection-a',status:'active',error:'',warnings:[],last_synced_at:null,
    accounts:[{id:'11111111-1111-4111-8111-111111111111',advertiser_id:'9007199254740993',
      advertiser_name:'测试账号',source_account_id:'100',source_account_name:'测试店铺',source_account_type:'SHOP'}]}],
};

afterEach(()=>{cleanup();vi.clearAllMocks()});

describe('千川计划只读目录',()=>{
  it('总开关关闭时不读取账号且不影响原页面',async()=>{
    api.planCapabilities.mockResolvedValue({...gate,enabled:false});
    const {container}=render(<QianchuanPlanCatalog/>);
    await waitFor(()=>expect(api.planCapabilities).toHaveBeenCalledOnce());
    expect(api.accounts).not.toHaveBeenCalled();
    expect(container.textContent).toBe('');
  });

  it('按账号、抖音号和商品顺序完成只读选择',async()=>{
    api.planCapabilities.mockResolvedValue(gate);
    api.accounts.mockResolvedValue(accounts);
    api.awemes.mockResolvedValue({items:[{
      id:'9007199254740994',name:'测试抖音号',show_id:'douyin-test',avatar_url:'',
      status:'AWEME_STATUS_NORMAL',has_live_permission:true,has_video_permission:true,
      has_publish_permission:false,bind_types:['OFFICIAL'],
    }],page:1,page_size:100,total_number:1,total_page:1});
    api.products.mockResolvedValue({items:[{
      id:'9007199254740995',name:'测试商品',image_url:'',category_name:'护肤',channel_type:'SHOP',
    }],cursor:1,has_more:false,page_size:50});

    render(<QianchuanPlanCatalog/>);
    expect(await screen.findByRole('region',{name:'千川计划目录测试'})).toBeTruthy();
    await waitFor(()=>expect(api.awemes).toHaveBeenCalledWith(
      '11111111-1111-4111-8111-111111111111',{keyword:'',page:1,page_size:100}));
    const aweme=await screen.findByRole('combobox',{name:'授权抖音号'});
    fireEvent.change(aweme,{target:{value:'9007199254740994'}});
    await waitFor(()=>expect(api.products).toHaveBeenCalledWith(
      '11111111-1111-4111-8111-111111111111',
      {aweme_id:'9007199254740994',keyword:'',cursor:undefined,page_size:50}));
    const product=await screen.findByRole('combobox',{name:'可投商品'});
    fireEvent.change(product,{target:{value:'9007199254740995'}});
    expect(screen.getByText(/抖音号：测试抖音号 · 9007199254740994/)).toBeTruthy();
    expect(screen.getByText(/商品：测试商品 · 9007199254740995/)).toBeTruthy();
    expect(screen.getByText('写入与 worker 均关闭')).toBeTruthy();
    expect(screen.queryByRole('button',{name:'加载更多商品'})).toBeNull();
  });

  it('开启只读功能但没有本人账号时给出可行动提示',async()=>{
    api.planCapabilities.mockResolvedValue(gate);
    api.accounts.mockResolvedValue({...accounts,connections:[]});
    render(<QianchuanPlanCatalog/>);
    expect(await screen.findByText('当前没有本人可用的千川账号，请先绑定或同步账号。')).toBeTruthy();
    expect(api.awemes).not.toHaveBeenCalled();
  });

  it('使用平台返回的 cursor 追加商品而不覆盖首批结果',async()=>{
    api.planCapabilities.mockResolvedValue(gate);
    api.accounts.mockResolvedValue(accounts);
    api.awemes.mockResolvedValue({items:[{
      id:'9007199254740994',name:'测试抖音号',show_id:'',avatar_url:'',status:'',
      has_live_permission:true,has_video_permission:true,has_publish_permission:false,bind_types:[],
    }],page:1,page_size:100,total_number:1,total_page:1});
    api.products
      .mockResolvedValueOnce({items:[{id:'9007199254740995',name:'商品一',image_url:'',category_name:'',channel_type:''}],
        cursor:51,has_more:true,page_size:50})
      .mockResolvedValueOnce({items:[{id:'9007199254740996',name:'商品二',image_url:'',category_name:'',channel_type:''}],
        cursor:101,has_more:false,page_size:50});
    render(<QianchuanPlanCatalog/>);
    const aweme=await screen.findByRole('combobox',{name:'授权抖音号'});
    fireEvent.change(aweme,{target:{value:'9007199254740994'}});
    const more=await screen.findByRole('button',{name:'加载更多商品'});
    fireEvent.click(more);
    await waitFor(()=>expect(api.products).toHaveBeenLastCalledWith(
      '11111111-1111-4111-8111-111111111111',
      {aweme_id:'9007199254740994',keyword:'',cursor:51,page_size:50}));
    await waitFor(()=>expect(screen.getByRole('option',{name:/商品二/})).toBeTruthy());
    expect(screen.getByRole('option',{name:/商品一/})).toBeTruthy();
  });

  it('一次点击自动走完单视频上传、建计划、审核和投流链路',async()=>{
    api.planCapabilities.mockResolvedValue(gate);
    api.accounts.mockResolvedValue(accounts);
    api.awemes.mockResolvedValue({items:[{
      id:'9007199254740994',name:'测试抖音号',show_id:'douyin-test',avatar_url:'',status:'',
      has_live_permission:true,has_video_permission:true,has_publish_permission:false,bind_types:[],
    }],page:1,page_size:100,total_number:1,total_page:1});
    api.products.mockResolvedValue({items:[{
      id:'9007199254740995',name:'测试商品',image_url:'',category_name:'护肤',channel_type:'SHOP',
    }],cursor:1,has_more:false,page_size:50});
    const uploads=[{
      id:'22222222-2222-4222-8222-222222222222',video_asset_id:78,
      binding_id:'11111111-1111-4111-8111-111111111111',advertiser_id:'9007199254740993',
      advertiser_name:'测试账号',filename:'测试成片.mp4',is_aigc:false,status:'succeeded',phase:'',
      material_id:'7688613085071491098',video_id:'video-test',error_message:'',
      created_at:'2026-09-24T10:00:00',updated_at:'2026-09-24T10:00:00',
      auto_pending:false,can_retry:false,can_verify:false,
    }];

    const onPlanCompleted=vi.fn();
    render(<QianchuanPlanCatalog uploads={uploads} onPlanCompleted={onPlanCompleted}/>);
    const aweme=await screen.findByRole('combobox',{name:'授权抖音号'});
    await waitFor(()=>expect(aweme).not.toHaveProperty('disabled',true));
    fireEvent.change(aweme,{target:{value:'9007199254740994'}});
    await waitFor(()=>expect(api.products).toHaveBeenCalledWith(
      '11111111-1111-4111-8111-111111111111',
      {aweme_id:'9007199254740994',keyword:'',cursor:undefined,page_size:50}));
    await screen.findByRole('option',{name:/测试商品/});
    fireEvent.change(screen.getByRole('combobox',{name:'可投商品'}),
      {target:{value:'9007199254740995'}});
    expect(await screen.findByRole('region',{name:'千川全域计划 Mock 演示'})).toBeTruthy();

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole('button',{name:'创建并投流'}));
    expect(screen.getByRole('status').textContent).toBe('素材上传中');
    expect((screen.getByRole('button',{name:/正在自动执行/}) as HTMLButtonElement).disabled).toBe(true);
    for(let step=0;step<6;step+=1){
      await act(async()=>{await vi.runOnlyPendingTimersAsync()});
    }
    vi.useRealTimers();
    expect(screen.getByRole('status').textContent).toBe('投放中');
    expect(screen.getByRole('region',{name:'Mock 计划数据'})).toBeTruthy();
    expect(screen.getByText('固定演示数据 · 非千川返回')).toBeTruthy();
    expect(screen.getByText('Mock 计划已启用并进入投放中')).toBeTruthy();
    expect(screen.getByRole('button',{name:'重新演示'})).toBeTruthy();
    expect(onPlanCompleted).toHaveBeenCalledOnce();
    expect(onPlanCompleted.mock.calls[0][0]).toMatchObject({status:'enabled',material_id:'7688613085071491098'});
  });

  it('平台目录失败时可以使用明确标识的 Mock 目录完成演示',async()=>{
    api.planCapabilities.mockResolvedValue(gate);
    api.accounts.mockResolvedValue(accounts);
    api.awemes.mockRejectedValue(new Error('provider unavailable'));
    const uploads=[{
      id:'22222222-2222-4222-8222-222222222222',video_asset_id:78,
      binding_id:'11111111-1111-4111-8111-111111111111',advertiser_id:'9007199254740993',
      advertiser_name:'测试账号',filename:'测试成片.mp4',is_aigc:false,status:'succeeded',phase:'',
      material_id:'7688613085071491098',video_id:'video-test',error_message:'',
      created_at:'2026-09-24T10:00:00',updated_at:'2026-09-24T10:00:00',
      auto_pending:false,can_retry:false,can_verify:false,
    }];
    render(<QianchuanPlanCatalog uploads={uploads}/>);
    expect((await screen.findByRole('alert')).textContent).toContain('目录请求失败');
    fireEvent.click(screen.getByRole('button',{name:'使用 Mock 目录继续演示'}));
    expect(screen.getByRole('option',{name:/Mock 授权抖音号/})).toBeTruthy();
    expect(screen.getByRole('option',{name:/Mock 可投商品/})).toBeTruthy();
    expect(await screen.findByRole('region',{name:'千川全域计划 Mock 演示'})).toBeTruthy();
    expect(screen.getByText(/抖音号和商品为 Mock 目录数据/)).toBeTruthy();
  });

  it('目录账号与历史上传 binding_id 口径不同时按 advertiser_id 复用成功素材',async()=>{
    api.planCapabilities.mockResolvedValue(gate);
    api.accounts.mockResolvedValue(accounts);
    api.awemes.mockRejectedValue(new Error('provider unavailable'));
    const uploads=[{
      id:'22222222-2222-4222-8222-222222222222',video_asset_id:78,
      binding_id:'connection-level-id',advertiser_id:'9007199254740993',
      advertiser_name:'测试账号',filename:'测试成片.mp4',is_aigc:false,status:'succeeded',phase:'',
      material_id:'7688613085071491098',video_id:'video-test',error_message:'',
      created_at:'2026-09-24T10:00:00',updated_at:'2026-09-24T10:00:00',
      auto_pending:false,can_retry:false,can_verify:false,
    }];
    render(<QianchuanPlanCatalog uploads={uploads}/>);
    fireEvent.click(await screen.findByRole('button',{name:'使用 Mock 目录继续演示'}));
    expect(await screen.findByRole('region',{name:'千川全域计划 Mock 演示'})).toBeTruthy();
    expect(screen.getByRole('region',{name:'千川全域计划 Mock 演示'}).textContent)
      .toContain('7688613085071491098');
  });
});
