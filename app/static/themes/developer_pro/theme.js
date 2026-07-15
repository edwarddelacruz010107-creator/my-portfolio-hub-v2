(function(){
  'use strict';
  function readData(){try{return JSON.parse((document.getElementById('developerProData')||{}).textContent||'{}');}catch(error){return{};}}
  var data=readData();
  var skills=Array.isArray(data.skills)?data.skills:[];
  var projects=Array.isArray(data.projects)?data.projects:[];
  var experiences=Array.isArray(data.experiences)?data.experiences:[];
  var profile=data.profile&&typeof data.profile==='object'?data.profile:{};
  var colors=['green','cyan','purple','red'];

  function node(tag,className,text){var item=document.createElement(tag);if(className)item.className=className;if(text!==undefined)item.textContent=String(text);return item;}
  function empty(container,message){container.replaceChildren(node('div','developer-pro-empty',message));}
  function safeHttpUrl(value){try{var url=new URL(String(value||''),window.location.origin);return url.protocol==='http:'||url.protocol==='https:'?url.href:'';}catch(error){return'';}}

  function preloader(){
    var root=document.getElementById('preloader'),bar=document.getElementById('bootProgress'),main=document.getElementById('mainContent');
    if(!root){if(main)main.classList.add('loaded');return;}
    var lines=Array.prototype.slice.call(root.querySelectorAll('.boot-line'));
    lines.forEach(function(line,index){window.setTimeout(function(){line.classList.add('visible');if(bar)bar.style.width=((index+1)/Math.max(lines.length,1)*100)+'%';if(index===lines.length-1){window.setTimeout(function(){root.classList.add('done');if(main)main.classList.add('loaded');window.setTimeout(function(){root.hidden=true;},600);},300);}},Number(line.dataset.delay)||index*200);});
  }

  function navigation(){
    var button=document.getElementById('navHamburger'),menu=document.getElementById('mobileMenu'),nav=document.getElementById('navbar');
    function close(){if(menu)menu.classList.remove('open');if(button)button.setAttribute('aria-expanded','false');}
    if(button&&menu)button.addEventListener('click',function(){var open=!menu.classList.contains('open');menu.classList.toggle('open',open);button.setAttribute('aria-expanded',open?'true':'false');});
    document.querySelectorAll('a[href^="#"]').forEach(function(link){link.addEventListener('click',close);});
    document.addEventListener('keydown',function(event){if(event.key==='Escape')close();});
    window.addEventListener('scroll',function(){if(nav)nav.classList.toggle('scrolled',window.scrollY>40);},{passive:true});
  }

  function typing(){
    var target=document.getElementById('typedText');if(!target)return;
    var phrase=String(profile.description||'').trim();
    if(!phrase){target.textContent='Portfolio details have not been added yet.';return;}
    var index=0;function step(){target.textContent=phrase.slice(0,index);if(index<=phrase.length){index+=1;window.setTimeout(step,24);}}step();
  }

  function heroLogs(){
    var target=document.getElementById('heroLogs');if(!target)return;
    ['[PORTFOLIO] Public profile loaded','[CONTENT] Showing owner-published work only','[CONTACT] Secure inquiry channel available'].forEach(function(message,index){window.setTimeout(function(){target.appendChild(node('div','hero-log-line',message));},index*180);});
  }

  function reveal(){
    var items=document.querySelectorAll('.reveal');
    if(!('IntersectionObserver'in window)){items.forEach(function(item){item.classList.add('visible');});return;}
    var observer=new IntersectionObserver(function(entries){entries.forEach(function(entry){if(entry.isIntersecting){entry.target.classList.add('visible');observer.unobserve(entry.target);}});},{threshold:.12});
    items.forEach(function(item){observer.observe(item);});
  }

  function counters(){document.querySelectorAll('[data-target]').forEach(function(item){var target=Math.max(0,Number(item.dataset.target)||0);item.textContent=String(target);});}

  function renderSkills(){
    var grid=document.getElementById('skillsGrid');if(!grid)return;grid.replaceChildren();
    if(!skills.length){empty(grid,'No skills have been published yet.');return;}
    skills.forEach(function(group,index){
      var card=node('article','skill-category reveal');
      var header=node('div','skill-cat-header');header.appendChild(node('div','skill-cat-icon '+colors[index%colors.length],'<>'));header.appendChild(node('div','skill-cat-name',group.category||group.name||'Skills'));card.appendChild(header);
      var entries=Array.isArray(group.skills)?group.skills:[];
      entries.forEach(function(skill){var level=Math.max(0,Math.min(100,Number(skill.level)||0));var row=node('div','skill-item');var rowHead=node('div','skill-item-header');rowHead.appendChild(node('span','skill-item-name',skill.name||'Unnamed skill'));rowHead.appendChild(node('span','skill-item-pct',level+'%'));row.appendChild(rowHead);var bar=node('div','skill-bar');var fill=node('div','skill-bar-fill '+colors[index%colors.length]);fill.dataset.level=String(level);fill.style.width=level+'%';bar.appendChild(fill);row.appendChild(bar);card.appendChild(row);});
      grid.appendChild(card);
    });
  }

  function projectLink(label,value){var href=safeHttpUrl(value);if(!href&&String(value||'').charAt(0)==='/')href=String(value);if(!href)return null;var link=node('a','project-link',label);link.href=href;if(/^https?:/i.test(href)){link.target='_blank';link.rel='noopener';}return link;}

  function renderProjects(){
    var grid=document.getElementById('projectsGrid');if(!grid)return;grid.replaceChildren();
    if(!projects.length){empty(grid,'No projects have been published yet.');return;}
    projects.forEach(function(project){
      var card=node('article','project-card reveal');card.dataset.category=String(project.category||'').toLowerCase();
      var media=node('div','project-img');
      if(project.image_url){var image=node('img');image.src=project.image_url;image.alt=project.image_alt||String(project.title||'Project')+' image';image.loading='lazy';media.appendChild(image);}else{media.appendChild(node('div','project-img-placeholder','No project image supplied'));}
      media.appendChild(node('div','scan-overlay'));card.appendChild(media);
      var body=node('div','project-body');body.appendChild(node('h3','',project.title||'Untitled project'));body.appendChild(node('p','',project.description_short||project.description||'No project description supplied.'));
      var tech=node('div','project-tech');(Array.isArray(project.tech_stack)?project.tech_stack:[]).forEach(function(item){tech.appendChild(node('span','',item));});body.appendChild(tech);
      var links=node('div','project-links');[
        project.case_study_enabled&&projectLink('Case study',project.case_study_url||project.url),
        projectLink('Live demo',project.demo_url),projectLink('Source',project.github_url),projectLink('Prototype',project.prototype_url)
      ].filter(Boolean).forEach(function(link){links.appendChild(link);});body.appendChild(links);card.appendChild(body);grid.appendChild(card);
    });
    document.querySelectorAll('.filter-btn').forEach(function(button){button.addEventListener('click',function(){document.querySelectorAll('.filter-btn').forEach(function(item){item.classList.remove('active');});button.classList.add('active');var filter=button.dataset.filter;grid.querySelectorAll('.project-card').forEach(function(card){card.classList.toggle('hidden-card',filter!=='all'&&card.dataset.category!==filter);});});});
  }

  function renderTimeline(){
    var timeline=document.getElementById('timeline');if(!timeline)return;timeline.replaceChildren();
    experiences.forEach(function(exp,index){var item=node('article','timeline-item reveal');item.appendChild(node('div','timeline-node '+colors[index%colors.length]));item.appendChild(node('div','timeline-year',exp.date_range||exp.year||''));item.appendChild(node('div','timeline-title',exp.role||exp.title||''));item.appendChild(node('div','timeline-company',exp.company||''));item.appendChild(node('div','timeline-desc',exp.description||''));item.appendChild(node('span','timeline-type',exp.type||exp.employment_type||'Work'));timeline.appendChild(item);});
  }

  function contact(){
    var form=document.getElementById('contactForm'),status=document.getElementById('formStatus'),button=document.getElementById('btnSend'),label=document.getElementById('btnSendText');if(!form)return;
    form.addEventListener('submit',function(event){event.preventDefault();if(!form.checkValidity()){form.reportValidity();return;}var rawAction=form.getAttribute('action')||'';if(!rawAction||rawAction==='#'){if(status){status.textContent='Contact delivery is not configured.';status.classList.add('active');}return;}if(button)button.disabled=true;if(label)label.textContent='Transmitting...';fetch(rawAction,{method:'POST',body:new FormData(form),headers:{Accept:'application/json'}}).then(function(response){return response.json().catch(function(){return{status:response.ok?'success':'error',message:response.ok?'Message sent.':'Submission failed.'};}).then(function(payload){if(!response.ok||payload.status==='error')throw new Error(payload.message||'Submission failed.');return payload;});}).then(function(payload){if(status){status.textContent=payload.message||'Message sent.';status.classList.add('active');}form.reset();}).catch(function(error){if(status){status.textContent=error.message||'Submission failed.';status.classList.add('active');}}).finally(function(){if(button)button.disabled=false;if(label)label.textContent='Transmit';});});
  }

  function terminal(){
    var toggle=document.getElementById('termToggle'),widget=document.getElementById('termWidget'),body=document.getElementById('termBody'),input=document.getElementById('termInput');if(!toggle||!widget||!body||!input)return;
    function output(message,className){body.appendChild(node('div','cmd-output'+(className?' '+className:''),message));body.scrollTop=body.scrollHeight;}
    toggle.addEventListener('click',function(){var open=widget.classList.toggle('open');if(open)input.focus();});
    input.addEventListener('keydown',function(event){if(event.key!=='Enter')return;var command=input.value.trim().toLowerCase();input.value='';output('$ '+command,'white');if(command==='clear'){body.replaceChildren();return;}if(command==='exit'){widget.classList.remove('open');return;}if(command==='help'){output('help, whoami, skills, projects, status, clear, exit');return;}if(command==='whoami'){output(String(profile.name||'Portfolio owner'),'green');output('Role: '+String(profile.title||'Not specified'),'cyan');return;}if(command==='skills'){output('Published skills: '+skills.reduce(function(total,group){return total+(Array.isArray(group.skills)?group.skills.length:0);},0));return;}if(command==='projects'){projects.forEach(function(project){output('› '+String(project.title||'Untitled project'));});return;}if(command==='status'){output('Published projects: '+projects.length,'green');return;}if(command)output('Command not found: '+command,'red');});
    document.addEventListener('keydown',function(event){if(event.key==='Escape')widget.classList.remove('open');});
  }

  document.addEventListener('DOMContentLoaded',function(){preloader();navigation();typing();heroLogs();renderSkills();renderProjects();renderTimeline();counters();contact();terminal();reveal();});
})();
