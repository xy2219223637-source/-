import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ImagePlus, Link2, Loader2, Upload, X } from 'lucide-react';
import { ecommerceProductApi, mediaUrl } from '../api/client';

export default function ProductCreate() {
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [category, setCategory] = useState('');
  const [price, setPrice] = useState('');
  const [description, setDescription] = useState('');
  const [targetUsers, setTargetUsers] = useState('');
  const [brandName, setBrandName] = useState('');
  const [ingredients, setIngredients] = useState('');
  const [usageMethod, setUsageMethod] = useState('');
  const [specifications, setSpecifications] = useState('');
  const [imageUrls, setImageUrls] = useState<string[]>([]);
  const [detailImageUrls, setDetailImageUrls] = useState<string[]>([]);
  const [urlInput, setUrlInput] = useState('');
  const [urlTarget, setUrlTarget] = useState<'product' | 'detail'>('product');
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');

  async function handleUpload(files: FileList | null, target: 'product' | 'detail') {
    if (!files?.length) return;
    setUploading(true);
    setError('');
    try {
      const urls: string[] = [];
      for (const file of Array.from(files)) {
        const url = await ecommerceProductApi.uploadImage(file);
        urls.push(url);
      }
      if (target === 'product') setImageUrls((prev) => [...prev, ...urls]);
      else setDetailImageUrls((prev) => [...prev, ...urls]);
    } catch (e) {
      console.error(e);
      setError('图片上传失败');
    } finally {
      setUploading(false);
    }
  }

  function addUrl() {
    const u = urlInput.trim();
    if (!u) return;
    if (urlTarget === 'product') setImageUrls((prev) => [...prev, u]);
    else setDetailImageUrls((prev) => [...prev, u]);
    setUrlInput('');
  }

  function removeUrl(target: 'product' | 'detail', idx: number) {
    if (target === 'product') setImageUrls((prev) => prev.filter((_, i) => i !== idx));
    else setDetailImageUrls((prev) => prev.filter((_, i) => i !== idx));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError('请填写商品名称');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const product = await ecommerceProductApi.create({
        name: name.trim(),
        category: category.trim(),
        price: Number(price) || 0,
        description: description.trim(),
        target_users: targetUsers.trim(),
        brand_name: brandName.trim(),
        ingredients: ingredients.trim(),
        usage_method: usageMethod.trim(),
        specifications: specifications.trim(),
        image_urls: imageUrls,
        detail_image_urls: detailImageUrls,
      });
      navigate(`/ecommerce-products/${product.id}`);
    } catch (err) {
      console.error(err);
      setError('创建失败，请检查后端服务');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-3xl animate-fade-in">
      <header className="mb-8">
        <h1 className="font-display text-3xl tracking-tight">创建电商商品</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          填写电商商品基础信息并上传图片
        </p>
      </header>

      <form className="space-y-6" onSubmit={(e) => onSubmit(e)}>
        <div className="panel p-6 space-y-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label className="field">
              <span>商品名称 *</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：云感恒温保温杯" required />
            </label>
            <label className="field">
              <span>商品类别</span>
              <input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="例如：生活日用 / 保温杯" />
            </label>
            <label className="field">
              <span>品牌名称</span>
              <input value={brandName} onChange={(e) => setBrandName(e.target.value)} placeholder="例如：品牌名 / 系列名" />
            </label>
            <label className="field">
              <span>价格（元）</span>
              <input type="number" min="0" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="199" />
            </label>
            <label className="field">
              <span>目标用户</span>
              <input value={targetUsers} onChange={(e) => setTargetUsers(e.target.value)} placeholder="例如：都市白领、通勤族" />
            </label>
          </div>
          <label className="field">
            <span>商品描述</span>
            <textarea
              rows={5}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="材质、规格、功能卖点、使用体验等…"
            />
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label className="field">
              <span>核心成分 / 配方</span>
              <textarea rows={4} value={ingredients} onChange={(e) => setIngredients(e.target.value)} placeholder="例如：烟酰胺、玻尿酸及对应功效依据" />
            </label>
            <label className="field">
              <span>使用方法</span>
              <textarea rows={4} value={usageMethod} onChange={(e) => setUsageMethod(e.target.value)} placeholder="使用步骤、频次、注意事项" />
            </label>
          </div>
          <label className="field">
            <span>规格信息</span>
            <textarea rows={3} value={specifications} onChange={(e) => setSpecifications(e.target.value)} placeholder="容量、包装、保质期、适用肤质等" />
          </label>
        </div>

        <div className="panel p-6 space-y-4">
          <h2 className="font-medium flex items-center gap-2">
            <ImagePlus size={18} /> 商品图片
          </h2>
          <ImageUploader
            urls={imageUrls}
            uploading={uploading}
            onUpload={(files) => handleUpload(files, 'product')}
            onRemove={(i) => removeUrl('product', i)}
          />

          <h2 className="font-medium flex items-center gap-2 pt-2">
            <ImagePlus size={18} /> 详情图片
          </h2>
          <ImageUploader
            urls={detailImageUrls}
            uploading={uploading}
            onUpload={(files) => handleUpload(files, 'detail')}
            onRemove={(i) => removeUrl('detail', i)}
          />

          <div className="flex flex-wrap gap-2 items-end pt-2 border-t border-[var(--border)]">
            <label className="field flex-1 min-w-[200px]">
              <span className="flex items-center gap-1"><Link2 size={14} /> 添加图片 URL</span>
              <input
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                placeholder="https://..."
              />
            </label>
            <select
              className="input-select"
              value={urlTarget}
              onChange={(e) => setUrlTarget(e.target.value as 'product' | 'detail')}
            >
              <option value="product">商品图</option>
              <option value="detail">详情图</option>
            </select>
            <button type="button" className="btn-secondary" onClick={addUrl}>
              添加
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 text-red-700 px-4 py-3 text-sm">
            {error}
          </div>
        )}

        <div className="flex flex-wrap gap-3">
          <button type="submit" className="btn-secondary" disabled={submitting}>
            {submitting ? <Loader2 className="animate-spin" size={16} /> : null}
            保存商品
          </button>

        </div>
      </form>
    </div>
  );
}

function ImageUploader({
  urls,
  uploading,
  onUpload,
  onRemove,
}: {
  urls: string[];
  uploading: boolean;
  onUpload: (files: FileList | null) => void;
  onRemove: (idx: number) => void;
}) {
  const [dragging, setDragging] = useState(false);

  function handleDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    event.stopPropagation();
    setDragging(false);
    if (!uploading) onUpload(event.dataTransfer.files);
  }

  return (
    <div>
      <label
        className={`upload-zone ${dragging ? 'upload-zone-dragging' : ''}`}
        onDragEnter={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'copy';
          setDragging(true);
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            setDragging(false);
          }
        }}
        onDrop={handleDrop}
      >
        <input
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(e) => onUpload(e.target.files)}
        />
        {uploading ? (
          <span className="inline-flex items-center gap-2 text-sm text-[var(--muted)]">
            <Loader2 className="animate-spin" size={16} /> 上传中…
          </span>
        ) : (
          <span className="inline-flex items-center gap-2 text-sm text-[var(--muted)]">
            <Upload size={16} /> {dragging ? '松开即可上传' : '点击或拖拽上传图片'}
          </span>
        )}
      </label>
      {urls.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-3">
          {urls.map((url, i) => (
            <div key={`${url}-${i}`} className="relative group w-24 h-24 rounded-lg overflow-hidden border border-[var(--border)] bg-[var(--bg-elevated)]">
              <img src={mediaUrl(url)} alt="" className="w-full h-full object-cover" />
              <button
                type="button"
                className="absolute top-1 right-1 p-1 rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 transition-opacity"
                onClick={() => onRemove(i)}
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
