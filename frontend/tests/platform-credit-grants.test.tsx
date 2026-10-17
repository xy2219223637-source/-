import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import PlatformAdmin from '../src/pages/PlatformAdmin';
import PlatformCreditGrants from '../src/components/PlatformCreditGrants';
import { api, authApi, platformAdminApi } from '../src/api/client';

vi.mock('../src/api/client',()=>({ api:{get:vi.fn(),post:vi.fn()}, authApi:{me:vi.fn()}, platformAdminApi:{listTenants:vi.fn()} }));
const tenant={id:'enterprise-b',name:'其他企业',code:'b',status:'active',member_count:2,owner_name:'管理员',owner_phone:'13900000000',owner_user_id:'owner',owner_status:'active',created_at:'2026-10-17T00:00:00Z'};
beforeEach(()=>{
  sessionStorage.clear();
  vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
  vi.mocked(authApi.me).mockResolvedValue({user_id:'platform-user',phone:'',role:'owner',tenant_id:'platform',tenant_name:'平台',is_platform_admin:true,permissions:['*']});
  vi.mocked(platformAdminApi.listTenants).mockResolvedValue([tenant]);
  vi.mocked(api.get).mockResolvedValue({data:{items:[],total:0}});
  vi.mocked(api.post).mockResolvedValue({data:{amount_credits:'100.0000',balance_after_credits:'1100.0000'}});
});
afterEach(()=>{cleanup();sessionStorage.clear()});

function fill(){
  fireEvent.change(screen.getByLabelText('发放积分'),{target:{value:'100'}});
  fireEvent.change(screen.getByLabelText('发放原因'),{target:{value:'测试补充'}});
}

describe('平台管理员发放企业积分',()=>{
  it('从其他企业的操作列打开入口，并调用受控发放API',async()=>{
    render(<PlatformAdmin/>);
    fireEvent.click(await screen.findByRole('button',{name:'发放积分'}));
    expect(await screen.findByRole('region',{name:'向其他企业发放积分'})).toBeTruthy();
    fill(); fireEvent.click(screen.getByRole('button',{name:'确认发放'}));
    await screen.findByText('已向「其他企业」发放 100.0000 积分，发放后企业余额 1100.0000');
    const call=vi.mocked(api.post).mock.calls[0];
    expect(call[0]).toBe('/platform-admin/tenants/enterprise-b/credits/grants');
    expect(call[1]).toEqual({amount_credits:'100',reason:'测试补充'});
    expect(call[2]?.headers?.['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('非平台管理员即使有企业owner角色也没有发放入口',async()=>{
    vi.mocked(authApi.me).mockResolvedValue({user_id:'owner',phone:'',role:'owner',tenant_id:'b',tenant_name:'B',is_platform_admin:false,permissions:['*']});
    render(<PlatformAdmin/>);
    await screen.findByText('其他企业');
    expect(screen.queryByRole('button',{name:'发放积分'})).toBeNull();
    expect(screen.queryByRole('button',{name:'扣回积分'})).toBeNull();
  });

  it('不确定结果关闭重开后沿用同一请求和幂等键',async()=>{
    vi.mocked(api.post).mockRejectedValueOnce({isAxiosError:true,message:'network error'}).mockResolvedValueOnce({data:{amount_credits:'100.0000',balance_after_credits:'1100.0000'}});
    const first=render(<PlatformCreditGrants tenant={tenant} actorId="platform-user" onClose={()=>{}}/>);
    fill(); fireEvent.click(screen.getByRole('button',{name:'确认发放'}));
    await screen.findByRole('button',{name:'重试本次发放'});
    first.unmount();
    render(<PlatformCreditGrants tenant={tenant} actorId="platform-user" onClose={()=>{}}/>);
    expect((screen.getByLabelText('发放积分') as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button',{name:'重试本次发放'}));
    await screen.findByRole('status');
    const calls=vi.mocked(api.post).mock.calls;
    expect(calls[0][2]?.headers?.['Idempotency-Key']).toBe(calls[1][2]?.headers?.['Idempotency-Key']);
    expect(calls[0][1]).toEqual(calls[1][1]);
    expect(sessionStorage.length).toBe(0);
  });

  it('明确参数拒绝允许修正；成功后的记录加载错误不会误报发放失败',async()=>{
    vi.mocked(api.post).mockRejectedValueOnce({isAxiosError:true,response:{status:422,data:{detail:{message:'超过发放上限'}}}});
    render(<PlatformCreditGrants tenant={tenant} actorId="platform-user" onClose={()=>{}}/>);
    fill(); fireEvent.click(screen.getByRole('button',{name:'确认发放'}));
    await screen.findByText('超过发放上限');
    expect((screen.getByLabelText('发放积分') as HTMLInputElement).disabled).toBe(false);
    vi.mocked(api.post).mockResolvedValue({data:{amount_credits:'100.0000',balance_after_credits:'1100.0000'}});
    vi.mocked(api.get).mockRejectedValue({isAxiosError:true,response:{status:503,data:{detail:{message:'记录暂不可用'}}}});
    fireEvent.click(screen.getByRole('button',{name:'确认发放'}));
    expect(await screen.findByRole('status')).toBeTruthy();
    await waitFor(()=>expect(screen.getByText('记录加载失败：记录暂不可用')).toBeTruthy());
    expect(screen.queryByRole('alert')).toBeNull();
  });
  it('扣回使用独立入口和幂等请求，显示调整记录', async()=>{
    vi.mocked(api.get).mockImplementation(async path=>({data: path.endsWith('/credits') ? {balance_credits:'1100.0000',reserved_credits:'100.0000',available_credits:'1000.0000'} : {items:[],total:0}}));
    render(<PlatformAdmin/>);
    fireEvent.click(await screen.findByRole('button',{name:'扣回积分'}));
    await screen.findByText(/可扣回 1000.0000/);
    fireEvent.change(screen.getByLabelText('扣回积分'),{target:{value:'100'}});
    fireEvent.change(screen.getByLabelText('扣回原因'),{target:{value:'纠正多发'}});
    vi.mocked(api.post).mockRejectedValueOnce({isAxiosError:true,message:'network error'});
    fireEvent.click(screen.getByRole('button',{name:'确认扣回'}));
    await screen.findByRole('button',{name:'重试本次扣回'});
    fireEvent.click(screen.getByRole('button',{name:'重试本次扣回'}));
    await screen.findByRole('status');
    const calls=vi.mocked(api.post).mock.calls;
    expect(calls[0][0]).toBe('/platform-admin/tenants/enterprise-b/credits/revocations');
    expect(calls[0][1]).toEqual({amount_credits:'100',reason:'纠正多发'});
    expect(calls[0][2]?.headers?.['Idempotency-Key']).toBe(calls[1][2]?.headers?.['Idempotency-Key']);
    expect(screen.getByText('本月积分调整记录')).toBeTruthy();
  });

  it('明确的可用余额不足允许修正金额，不冻结为不确定请求', async()=>{
    vi.mocked(api.post).mockRejectedValueOnce({isAxiosError:true,response:{status:409,data:{detail:{code:'INSUFFICIENT_AVAILABLE_CREDITS',message:'可用积分不足'}}}});
    render(<PlatformCreditGrants tenant={tenant} actorId="platform-user" mode="revoke" onClose={()=>{}}/>);
    fireEvent.change(screen.getByLabelText('扣回积分'),{target:{value:'100'}});
    fireEvent.change(screen.getByLabelText('扣回原因'),{target:{value:'纠正多发'}});
    fireEvent.click(screen.getByRole('button',{name:'确认扣回'}));
    await screen.findByText('可用积分不足');
    expect((screen.getByLabelText('扣回积分') as HTMLInputElement).disabled).toBe(false);
    expect(sessionStorage.length).toBe(0);
  });

});
