import React, { useEffect, useState } from 'react';
import { api } from '../api';

export default function Products() {
  const [products, setProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  
  // Filters
  const [query, setQuery] = useState('');
  const [categoryId, setCategoryId] = useState('');
  const [vertical, setVertical] = useState('');
  const [hasBarcode, setHasBarcode] = useState('');
  const [isLoose, setIsLoose] = useState('');

  const VERTICALS = ['grocery', 'apparel', 'footwear', 'electronics', 'optical', 'services', 'general'];

  useEffect(() => {
    fetchCategories();
    fetchProducts();
  }, []);

  const fetchCategories = async () => {
    try {
      const data = await api.posCategories();
      setCategories(data.categories || []);
    } catch (e) {
      console.error("Failed to fetch categories", e);
    }
  };

  const fetchProducts = async () => {
    setLoading(true);
    try {
      const params = { limit: 50, offset: 0 };
      if (query) params.q = query;
      if (categoryId) params.category_id = categoryId;
      if (vertical) params.vertical = vertical;
      if (hasBarcode) params.has_barcode = hasBarcode;
      if (isLoose) params.is_loose = isLoose;

      const data = await api.adminProducts(params);
      setProducts(data.products || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = (e) => {
    e.preventDefault();
    fetchProducts();
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:justify-between md:items-end gap-4">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Product Catalog</h1>
          <p className="text-slate-500 text-xs mt-0.5">Global catalog across all verticals — filter by vertical, category or type.</p>
        </div>
        
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            type="text"
            placeholder="Search products..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
          />
          <button type="submit" className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors">
            Search
          </button>
        </form>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        {/* Filter Bar */}
        <div className="p-4 border-b border-slate-100 bg-slate-50/50 flex flex-wrap gap-4 items-center">
          <span className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Filters:</span>

          <select
            value={vertical}
            onChange={(e) => { setVertical(e.target.value); setTimeout(fetchProducts, 0); }}
            className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white capitalize"
          >
            <option value="">All Verticals</option>
            {VERTICALS.map(v => <option key={v} value={v}>{v}</option>)}
          </select>

          <select
            value={categoryId}
            onChange={(e) => { setCategoryId(e.target.value); setTimeout(fetchProducts, 0); }}
            className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white"
          >
            <option value="">All Categories</option>
            {categories.map(c => (
              <option key={c.category_id} value={c.category_id}>{c.name}</option>
            ))}
          </select>

          <select 
            value={hasBarcode} 
            onChange={(e) => { setHasBarcode(e.target.value); setTimeout(fetchProducts, 0); }}
            className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white"
          >
            <option value="">Barcode: Any</option>
            <option value="yes">Has Barcode</option>
            <option value="no">No Barcode</option>
          </select>

          <select 
            value={isLoose} 
            onChange={(e) => { setIsLoose(e.target.value); setTimeout(fetchProducts, 0); }}
            className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white"
          >
            <option value="">Type: Any</option>
            <option value="yes">Loose/Weighed</option>
            <option value="no">Packaged</option>
          </select>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs border-b border-slate-200">
              <tr>
                <th className="px-6 py-4">Image</th>
                <th className="px-6 py-4">Name & Brand</th>
                <th className="px-6 py-4">Vertical</th>
                <th className="px-6 py-4">Category</th>
                <th className="px-6 py-4">Barcode / SKU</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan="5" className="px-6 py-4 text-center text-slate-400">Loading products...</td></tr>
              ) : products.length === 0 ? (
                <tr><td colSpan="5" className="px-6 py-4 text-center text-slate-400">No products found matching filters.</td></tr>
              ) : (
                products.map(product => (
                  <tr key={product.product_id} className="hover:bg-slate-50/50">
                    <td className="px-6 py-4">
                      {product.image_url ? (
                        <img src={product.image_url} alt={product.name} className="w-10 h-10 object-cover rounded shadow-sm border border-slate-200" />
                      ) : (
                        <div className="w-10 h-10 bg-slate-100 rounded flex items-center justify-center text-xl">📦</div>
                      )}
                    </td>
                    <td className="px-6 py-4 font-medium text-slate-900">
                      {product.name}
                      <div className="text-xs text-slate-500 font-normal">{product.brand || 'No brand'} • {product.weight} {product.unit}</div>
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-indigo-50 text-indigo-700 capitalize">
                        {product.vertical_code || 'grocery'}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-700">
                        {product.category_name || 'Uncategorized'}
                      </span>
                    </td>
                    <td className="px-6 py-4 font-mono text-xs">
                      {product.barcode ? <div className="text-slate-900">{product.barcode}</div> : <div className="text-slate-400 italic">No barcode</div>}
                      {product.sku && <div className="text-slate-500 mt-1">SKU: {product.sku}</div>}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
