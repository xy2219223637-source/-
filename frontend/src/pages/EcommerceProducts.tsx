import { useEffect, useState } from 'react';
import { ArrowRight, Box, Image as ImageIcon, Loader2, Plus } from 'lucide-react';
import { Link } from 'react-router-dom';
import { mediaUrl, ecommerceProductApi } from '../api/client';
import type { Product } from '../types';

function completeness(product: Product) {
  const fields = [product.name, product.category, product.brand_name, product.description, product.target_users, product.specifications];
  const filled = fields.filter((value) => String(value || '').trim()).length + (product.image_urls.length ? 1 : 0);
  return Math.round(filled / 7 * 100);
}

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    ecommerceProductApi.list().then(setProducts).catch(() => setError('商品列表加载失败')).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="h-64 grid place-items-center text-[var(--muted)]"><Loader2 className="animate-spin" /></div>;

  return <div className="space-y-6 animate-fade-in">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><div className="text-xs font-medium text-[var(--accent)]">PRODUCTS</div><h1 className="font-display text-4xl mt-2">电商商品管理</h1><p className="text-sm text-[var(--muted)] mt-2">独立管理电商业务的商品资料与素材。</p></div>
      <Link to="/ecommerce-products/new" className="btn-primary"><Plus size={15}/>创建商品</Link>
    </header>
    {error && <div className="rounded-xl border border-red-200 bg-red-50 text-red-700 px-5 py-4">{error}</div>}
    {products.length === 0 ? <section className="panel py-16 text-center"><Box size={28} className="mx-auto text-[var(--muted)]"/><p className="text-sm text-[var(--muted)] mt-3">还没有商品</p><Link to="/ecommerce-products/new" className="btn-primary mt-4 inline-flex"><Plus size={14}/>创建第一个商品</Link></section> :
      <section className="panel overflow-hidden"><div className="panel-header flex justify-between"><h2 className="font-medium">全部商品</h2><span className="text-xs text-[var(--muted)]">共 {products.length} 个</span></div><div className="divide-y divide-[var(--border)]">{products.map((product) => {
        const score = completeness(product); const cover = product.image_urls[0] || product.detail_image_urls[0];
        return <Link key={product.id} to={`/ecommerce-products/${product.id}`} className="grid sm:grid-cols-[64px_1fr_180px_120px_24px] gap-4 items-center px-5 py-4 hover:bg-[var(--bg-elevated)] transition-colors">
          {cover ? <img src={mediaUrl(cover)} alt="" className="w-16 h-16 rounded-lg object-cover border border-[var(--border)]"/> : <div className="w-16 h-16 rounded-lg bg-[var(--bg-elevated)] grid place-items-center text-[var(--muted)]"><ImageIcon size={20}/></div>}
          <div className="min-w-0"><div className="font-medium truncate">{product.name}</div><div className="text-xs text-[var(--muted)] mt-1">{product.brand_name || '未填写品牌'} · {product.category || '未分类'} · ¥{product.price}</div></div>
          <div><div className="flex justify-between text-[11px] text-[var(--muted)] mb-1"><span>资料完整度</span><span>{score}%</span></div><div className="h-1.5 rounded-full bg-[#e8e5de] overflow-hidden"><div className="h-full bg-[var(--accent)]" style={{width:`${score}%`}}/></div></div>
          <div className="text-xs text-[var(--muted)] sm:text-right">{new Date(product.updated_at).toLocaleDateString()} 更新</div><ArrowRight size={15} className="text-[var(--muted)]"/>
        </Link>;
      })}</div></section>}
  </div>;
}
