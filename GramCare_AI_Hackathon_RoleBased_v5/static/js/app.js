// GramCare UI helpers: offline status + lightweight local draft storage for encounter forms.
(function(){
  const update=()=>{document.querySelectorAll('.online').forEach(x=>x.innerHTML=navigator.onLine?'● System Online':'● Offline — drafts can be saved locally')};
  window.addEventListener('online',update); window.addEventListener('offline',update); update();
  const forms=document.querySelectorAll('form'); forms.forEach(form=>{ if(form.querySelector('[name="symptoms"]')){form.addEventListener('submit',()=>localStorage.removeItem('gramcare_encounter_draft')); const fields=form.querySelectorAll('input,textarea,select'); fields.forEach(f=>f.addEventListener('input',()=>{const d={};fields.forEach(x=>d[x.name]=x.value);localStorage.setItem('gramcare_encounter_draft',JSON.stringify(d))}));}});
})();
