import { ChevronRight, FolderOpen, Folder } from 'lucide-react';
import type { Row } from '../api';
export type KnowledgeCategory={id:string;name:string;children:KnowledgeCategory[]};
export type CategoryInfo={id:string;name:string;path:string;leaf:boolean};
export default function KnowledgeDirectories({tree,categories,rows,selected,onSelect}:{tree:KnowledgeCategory[];categories:CategoryInfo[];rows:Row[];selected:string;onSelect:(id:string)=>void}) {
  function count(id:string){const path=categories.find(c=>c.id===id)?.path;return rows.filter(r=>r.category_path===path||path&&r.category_path?.startsWith(path+'/')).length;}
  function render(nodes:KnowledgeCategory[]):any {
    return nodes.map(n=>n.children.length?<details key={n.id} className="kb-directory-branch" open><summary><ChevronRight size={13}/><button type="button" className={selected===n.id?'active':''} onClick={e=>{e.preventDefault();onSelect(n.id)}}><FolderOpen size={15}/><span>{n.name}</span><small>{count(n.id)}</small></button></summary><div className="kb-directory-children">{render(n.children)}</div></details>:<button type="button" key={n.id} className={'kb-directory-leaf '+(selected===n.id?'active':'')} aria-label={'目录 '+categories.find(c=>c.id===n.id)?.path} onClick={()=>onSelect(n.id)}><Folder size={14}/><span>{n.name}</span><small>{count(n.id)}</small></button>);
  }
  return <nav className="kb-directory-tree" aria-label="知识库目录"><div className="kb-directory-heading"><FolderOpen size={17}/><strong>知识库目录</strong></div><p>选择末级目录后导入资料</p><button className={'kb-directory-leaf '+(selected==='all'?'active':'')} onClick={()=>onSelect('all')}><FolderOpen size={15}/><span>全部资料</span><small>{rows.length}</small></button>{render(tree)}<button className={'kb-directory-leaf '+(selected==='unclassified'?'active':'')} onClick={()=>onSelect('unclassified')}><Folder size={15}/><span>待归类（历史资料）</span><small>{rows.filter(r=>!r.category_id).length}</small></button></nav>;
}
export function CategoryOptions({categories}:{categories:CategoryInfo[]}) {return <><option value="">请选择末级目录</option>{categories.filter(c=>c.leaf).map(c=><option key={c.id} value={c.id}>{c.path}</option>)}</>;}
