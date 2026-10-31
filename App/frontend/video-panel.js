'use strict';
(() => {
  const el = id => document.getElementById(id);
  let available = false, active = null, source = null, end = null, last = null, driving = null, drivingUrl = null, drivingSeconds = 0, subject = null;
  let polling = false;
  let timer = null, speechReference = null, speechAudio = null, recordingAudio = null;
  const characters=[];
  let profiles=[], selectedEngine='freevideo-h3-int8', engineDrafts={};
  const originalHints=Object.fromEntries(['videoQualityHelp','videoProfileHint','videoPromptGuide'].map(id=>[id,el(id).textContent]));
  const isWan=()=>el('videoEngine').value.startsWith('wan22-');
  const isLtx=()=>el('videoEngine').value==='ltx25-distilled';
  const isCharacter=()=>['wan-animate2','scail2'].includes(el('videoEngine').value);
  const isAnimate=()=>el('videoEngine').value==='wan-animate2';
  const isScail=()=>el('videoEngine').value==='scail2';
  // Wan Animate 2 and SCAIL-2: the person of a photo does what a reference video shows (docs/Wan-Animate-2.md, docs/SCAIL-2.md).
  const characterMode=()=>isCharacter() && ['animate','replace'].includes(el('videoMode').value);
  const isProfiled=()=>isWan() || isLtx() || isCharacter();
  const isS2V=()=>el('videoEngine').value==='wan22-s2v-14b';
  const engineName=id=>profiles.find(p=>p.id===id)?.name || ({'wan22-ti2v-5b':'Wan 2.2 TI2V-5B','wan22-s2v-14b':'Wan 2.2 S2V-14B'})[id] || 'FreeVideo';
  function rememberEngine(){engineDrafts[selectedEngine]={mode:el('videoMode').value,quality:el('videoQuality').value,frames:el('videoDuration').value,resolution:el('videoResolution').value,structure:el('videoStructure').value,scenes:el('videoSceneCount').value};}
  const plural=n=>n%10===1 && n%100!==11?StudioI18n.t("video.configure_engine_frame"):n%10>=2 && n%10<=4 && !(n%100>=12 && n%100<=14)?StudioI18n.t("video.duration_frame_count_few_plural_label"):StudioI18n.t("video.duration_frame_count_many_plural_label");
  const durationOption=(profile,n)=>StudioI18n.option(() => (profile.duration_labels?.[n] || (n===1440?StudioI18n.t("video.configure_engine_1_minute_6_scenes_1440_frames"):StudioI18n.t("video.configure_engine_s", [(n/profile.fps).toLocaleString(StudioI18n.locale,{maximumFractionDigits:4}), n, plural(n)]))), String(n));
  const wan5=()=>isWan() && !isS2V();
  // Engines that can build a long clip from one prompt: Wan 2.2 TI2V-5B and LTX 2.5 (continuation windows or an edit of scenes).
  const longEngine=()=>wan5() || isLtx();
  // Chain length per total frame count: Wan 121-frame segments (WAN_SEGMENTS) or LTX continuation windows (LTX_WINDOWS in video_registry.py).
  const chainSegments=n=>isLtx()?{360:4,720:8,1440:15}[n]:{240:2,360:3,720:6,1440:12}[n];
  // Wan 2.2 TI2V-5B makes about 5 seconds per generation. Longer clips are either one shot continued segment by segment
  // ("chain") or several independent shots joined with cuts ("scenes"); the duration list changes with that choice.
  function rebuildDurations(profile,keep=el('videoDuration').value){
    const kind=el('videoStructure').value;
    const list=kind==='chain'?profile.frames.filter(n=>chainSegments(n)):kind==='scenes'?profile.frames.filter(n=>n>=240):profile.frames.filter(n=>n<=121);
    el('videoDuration').replaceChildren(...list.map(n=>kind==='scenes'?StudioI18n.option(() => (StudioI18n.t("video.scene_duration_minimum_label",[n/profile.fps])), String(n)):durationOption(profile,n)));
    el('videoDuration').value=list.includes(Number(keep))?String(keep):{single:isLtx()?'49':'81',chain:'360',scenes:'720'}[kind];
  }
  function configureEngine(){
    selectedEngine=el('videoEngine').value;
    const profile=profiles.find(p=>p.id===selectedEngine);if(!profile)return;
    const saved=engineDrafts[selectedEngine] || {};
    // Modes an engine does not implement are hidden rather than greyed out, so the list shows only what works.
    for(const option of el('videoMode').options){option.disabled=!profile.modes.includes(option.value);option.hidden=option.disabled;}
    el('videoMode').value=profile.modes.includes(saved.mode)?saved.mode:profile.modes[0];
    el('videoDuration').replaceChildren(...profile.frames.map(n=>durationOption(profile,n)));
    if(profile.frames.includes(Number(saved.frames)))el('videoDuration').value=saved.frames;
    else if(isWan() && !isS2V())el('videoDuration').value='81';
    else if(isCharacter())el('videoDuration').value=String(profile.frames[profile.frames.length-1]);
    if(longEngine()){
      el('videoStructure').value=['single','chain','scenes'].includes(saved.structure)?saved.structure:'single';
      if(saved.scenes)el('videoSceneCount').value=saved.scenes;
      rebuildDurations(profile,saved.frames);
    }else if(isS2V())el('videoStructure').value=['single','scenes'].includes(saved.structure)?saved.structure:'single';
    el('videoResolution').replaceChildren(...profile.resolutions.map(([w,h])=>StudioI18n.option(() => (`${w} × ${h}`), `${w}x${h}`)));
    if([...el('videoResolution').options].some(o=>o.value===saved.resolution))el('videoResolution').value=saved.resolution;
    else if(isWan() && !isS2V())el('videoResolution').value='832x480';
    const names={preview:StudioI18n.t("video.generation_preview_quality_option"),standard:StudioI18n.t("video.generation_standard_quality_option"),high:StudioI18n.t("video.generation_high_quality_option")};
    const h3={preview:StudioI18n.t("video.configure_engine_8_steps"),standard:StudioI18n.t("video.configure_engine_8_3_steps"),high:StudioI18n.t("video.configure_engine_16_steps")};
    el('videoQuality').replaceChildren(...profile.qualities.map(q=>StudioI18n.option(() => (profile.quality_labels?.[q] || `${names[q]} · ${profile.steps?profile.steps[q]+StudioI18n.t("video.configure_engine_steps"):h3[q]}`), q)));
    el('videoQuality').value=profile.qualities.includes(saved.quality)?saved.quality:(isProfiled()?'standard':'preview');
    StudioI18n.bind(el('videoQuality').previousElementSibling, "title", () => (isProfiled()?StudioI18n.t("video.quality_previous_element_sibling_step_one_model_refinement_result_more"):StudioI18n.t("video.quality_previous_element_sibling_step_one_model_refinement_8_3")), true);
    StudioI18n.bind(el('videoQualityHelp'), "textContent", () => (isCharacter()?StudioI18n.t("video.quality_help_character"):isLtx()?StudioI18n.t("video.quality_help_fast_8_steps_one_pass_lightweight"):isS2V()?StudioI18n.t("video.quality_help_20_consecutive_model_refinements_scene_one"):isWan()?StudioI18n.t("video.quality_help_step_one_scene_refinement_start_standard"):originalHints.videoQualityHelp), true);
    StudioI18n.bind(el('videoProfileHint'), "textContent", () => (isCharacter()?StudioI18n.t("video.profile_hint_character"):isLtx()?StudioI18n.t("video.profile_hint_24_fps_start_768_512_49"):isS2V()?StudioI18n.t("video.profile_hint_16_fps_frames_per_second_short"):isWan()?StudioI18n.t("video.profile_hint_24_fps_frames_per_second_start"):originalHints.videoProfileHint), true);
    StudioI18n.bind(el('videoPromptGuide'), "textContent", () => (isAnimate()?StudioI18n.t("video.prompt_guide_animate2"):isScail()?StudioI18n.t("video.prompt_guide_scail2"):isLtx()?StudioI18n.t("video.prompt_guide_describe_scene_camera_movement_desired_sound"):isS2V()?StudioI18n.t("video.prompt_guide_describe_character_expression_gentle_movement_enter"):isWan()?StudioI18n.t("video.prompt_guide_wan_start_simple_english_scene_description"):originalHints.videoPromptGuide), true);
    available=profile.available;
    showRating(profile.rating);
    el('videoNegativeBlock').hidden=!profile.negative_prompt;
    // Per-engine strengths and limits; engines without an audited text keep the block hidden.
    const assessed={'wan22-ti2v-5b':'wan22','wan22-s2v-14b':'s2v','ltx25-distilled':'ltx','wan-animate2':'animate2','scail2':'scail2'}[selectedEngine];
    el('videoAssessment').hidden=!assessed;
    if(assessed)for(const [id,field] of Object.entries({videoAssessmentStrong:'strong',videoAssessmentVariable:'variable',videoAssessmentLimits:'limits',videoAssessmentEvidence:'evidence'}))StudioI18n.bind(el(id),'textContent',()=>StudioI18n.t(`video.assessment_${assessed}_${field}_text`),true);
    StudioI18n.bind(el('videoAvailability'), "textContent", () => (profile.available?`${profile.name} · ${StudioI18n.remote(profile.verification?.summary_ru) || (isProfiled()?StudioI18n.t("video.availability_local_generation_has_not_been_tested"):StudioI18n.t("video.availability_tested_rtx_5070_ti_16_gb"))}`:StudioI18n.remote(profile.reason)), true);
    StudioI18n.bind(el('videoModelDetails'), "textContent", () => ([profile.release_date?StudioI18n.t("video.model_details_release", [profile.release_date]):'',profile.requirements,profile.limitations].filter(Boolean).join(' ')), true);
    el('videoGenerate').disabled=!available || !!active;
    modes();
  }
  const structure=()=>!el('videoStructureBlock').hidden?el('videoStructure').value:'single';
  // Long Wan S2V clips take their length from the speech: a recording is measured in the browser, a typed line is estimated
  // (about 14 characters a second). The server plans the real segments after the speech exists and maps the prompts onto them.
  let speechAudioSeconds=0;
  const speechSeconds=()=>el('videoSpeechMode').value==='audio'?speechAudioSeconds:Math.max(1,el('videoSpeechText').value.length/14);
  const measureAudio=file=>new Promise(resolve=>{const url=URL.createObjectURL(file),audio=new Audio();audio.preload='metadata';const done=value=>{URL.revokeObjectURL(url);resolve(value);};audio.onloadedmetadata=()=>done(Number.isFinite(audio.duration)?audio.duration:0);audio.onerror=()=>done(0);audio.src=url;});
  // Scene layout mirrors scene_frames() in video_registry.py: the shortest 4n+1 frame count that reaches the minimum total.
  function sceneLayout(){
    const total=Number(el('videoDuration').value),n=Number(el('videoSceneCount').value);
    if(!Number.isInteger(n) || n<2 || n>30)return null;
    // Wan: 4n+1 frames, 33 at least; LTX: 8n+1 frames, 49 at least (scene_frames() in video_registry.py).
    const step=isLtx()?8:4,per=Math.max(isLtx()?49:33,Math.ceil((Math.ceil(total/n)-1)/step)*step+1);
    return {n,per,total:n*per,tooFew:per>121,minimum:Math.ceil(total/121)};
  }
  let sceneTouched=false;
  function suggestScenes(){
    // About 4.5 seconds per scene is comfortable for the model; the person's own number is never overwritten.
    if(!sceneTouched && structure()==='scenes')el('videoSceneCount').value=String(Math.max(2,Math.min(30,Math.ceil(Number(el('videoDuration').value)/108))));
  }
  function syncStructure(){
    const on=longEngine() && ['t2v','i2v'].includes(el('videoMode').value) || isS2V() && el('videoMode').value==='s2v';
    el('videoStructureBlock').hidden=!on;
    const kind=on?el('videoStructure').value:'single';
    const speech=isS2V();
    // A chain of continuing segments exists only for Wan 2.2 TI2V; the speech model builds long clips from scenes (docs/Wan-S2V.md).
    for(const option of el('videoStructure').options)option.hidden=speech && option.value==='chain';
    if(speech && el('videoStructure').value==='chain')el('videoStructure').value='scenes';
    el('videoScenesBlock').hidden=kind!=='scenes' || speech;
    el('videoDuration').disabled=speech && kind!=='single';
    el('videoSpeechText').maxLength=speech && kind!=='single'?1000:160;
    StudioI18n.bind(el('videoStructureHelp'),'textContent',()=>StudioI18n.t(`video.structure_${speech?'s2v_':isLtx()?'ltx_':''}${kind}_help`),true);
    el('videoSegmentInfo').hidden=!(speech && kind!=='single');
    StudioI18n.bind(el('videoSegmentInfo'),'textContent',()=>{
      const seconds=speechSeconds();
      return seconds?StudioI18n.t('video.s2v_segment_info',[seconds.toLocaleString(StudioI18n.locale,{maximumFractionDigits:1}),segmentCount()||1,seconds>75?StudioI18n.t('video.s2v_speech_too_long'):'']):StudioI18n.t('video.s2v_segment_info_no_speech');
    },true);
    StudioI18n.bind(el('videoSceneInfo'),'textContent',()=>{
      const layout=sceneLayout();
      return !layout?StudioI18n.t('video.scene_info_invalid'):layout.tooFew?StudioI18n.t('video.scene_info_too_few',[layout.minimum]):(isLtx() && layout.total>Number(el('videoDuration').value) ? StudioI18n.t('video.scene_info_trimmed_tails',[layout.n,layout.per,(layout.per/24).toLocaleString(StudioI18n.locale,{maximumFractionDigits:2}),Number(el('videoDuration').value),(Number(el('videoDuration').value)/24).toLocaleString(StudioI18n.locale,{maximumFractionDigits:1})]) : StudioI18n.t('video.scene_info_summary',[layout.n,layout.per,(layout.per/24).toLocaleString(StudioI18n.locale,{maximumFractionDigits:2}),layout.total,(layout.total/24).toLocaleString(StudioI18n.locale,{maximumFractionDigits:1})]));
    },true);
  }
  const segmentCount=()=>{
    const kind=structure();
    if(isS2V()){
      const seconds=speechSeconds();
      // Scenes are cut at pauses, so there are a little more of them than 4.8125 s slices would give.
      const n=kind==='scenes'?Math.ceil(seconds/4.4):0;
      return n<2?0:Math.min(16,n);
    }
    if(kind==='chain')return chainSegments(Number(el('videoDuration').value)) || 0;
    if(kind==='scenes')return sceneLayout()?.n || 0;
    return 0;
  };
  let plan={prompts:[],key:''};
  // Gemma rewrites the idea into the form the chosen model follows best (hidden per-model instruction on the server). The rewritten text is shown
  // and can be edited; the person's own words stay in the history. Scenarios (a segment plan) are already written by Gemma, so the block hides then.
  let enhanced={key:'',text:''};
  const enhanceModes=()=>profiles.find(item=>item.id===selectedEngine)?.prompt_enhancer || [];
  const enhanceOn=()=>!el('videoEnhanceBlock').hidden && el('videoEnhance').checked;
  const enhanceKey=()=>JSON.stringify([selectedEngine,el('videoMode').value,el('videoPrompt').value.trim(),!!source,!!end,drivingUrl,!!subject,el('videoReferenceObject').value,el('videoDrivingObject').value]);
  function syncEnhance(){
    el('videoEnhanceBlock').hidden=!enhanceModes().includes(el('videoMode').value) || !!segmentCount();
    el('videoEnhanceDetails').hidden=!el('videoEnhance').checked || !el('videoEnhanced').value.trim();
  }
  async function buildEnhanced(){
    if(!el('videoPrompt').value.trim()){
      const empty=new Error(StudioI18n.t('video.plan_enter_idea_first'));
      StudioI18n.bind(el('videoEnhanceStatus'),'textContent',()=>StudioI18n.remote(empty.message),true);
      throw empty;
    }
    if(characterMode()&&!driving){
      const missing=new Error(StudioI18n.t("video.generation_add_driving_video"));
      StudioI18n.bind(el('videoEnhanceStatus'),'textContent',()=>StudioI18n.remote(missing.message),true);
      throw missing;
    }
    el('videoEnhanceBuild').disabled=true;
    StudioI18n.bind(el('videoEnhanceStatus'),'textContent',()=>StudioI18n.t('video.enhance_prompt_status_working'),true);
    try{
      const body={prompt:el('videoPrompt').value,engine:selectedEngine,mode:el('videoMode').value};
      if(source&&['i2v','first_last','a2v','s2v','animate','replace'].includes(body.mode))body.source_image=await smallFrame();
      if(characterMode()){
        body.driving_frames=await drivingFrames().catch(()=>[]);   // Gemma still works from the photo when the browser cannot decode the video
        if(isScail()){
          body.objects={reference:el('videoReferenceObject').value,driving:el('videoDrivingObject').value};
          if(subject){body.subject_images=[await smallSubject()];body.objects.subjects=[{reference:el('videoSubjectReference').value,driving:el('videoSubjectDriving').value}];}
        }
      }
      if(end&&body.mode==='first_last')body.end_image=await smallEnd();
      const answer=await api('enhance',body);
      enhanced={key:enhanceKey(),text:answer.prompt};el('videoEnhanced').value=answer.prompt;el('videoEnhancedMotion').value=answer.motion_prompt||'';
      StudioI18n.bind(el('videoEnhanceStatus'),'textContent',()=>StudioI18n.t('video.enhance_prompt_status_ready'),true);
    }catch(error){
      StudioI18n.bind(el('videoEnhanceStatus'),'textContent',()=>StudioI18n.remote(error.message),true);
      throw error;
    }finally{el('videoEnhanceBuild').disabled=false;syncEnhance();}
  }
  const planKey=()=>JSON.stringify([selectedEngine,el('videoPrompt').value.trim(),structure(),segmentCount(),['i2v','s2v'].includes(el('videoMode').value)&&!!source,isS2V()&&el('videoSpeechMode').value==='clone'?el('videoSpeechText').value:'']);
  function renderPlan(){
    const count=segmentCount();
    el('videoPlanList').replaceChildren(...plan.prompts.map((text,index)=>{
      const wrap=document.createElement('div');
      const label=document.createElement('label');label.htmlFor='videoPlan-'+index;
      StudioI18n.bind(label,'textContent',()=>StudioI18n.t(structure()==='scenes'?'video.plan_scene_label':'video.plan_segment_label',[index+1,count]),true);
      const area=document.createElement('textarea');area.id='videoPlan-'+index;area.rows=4;area.maxLength=1500;area.value=text;
      area.oninput=()=>{plan.prompts[index]=area.value;};
      wrap.append(label,area);return wrap;
    }));
  }
  function syncPlan(){
    syncStructure();
    const count=segmentCount();
    el('videoPlanBlock').hidden=!count;
    StudioI18n.bind(el('videoPlanTitle'),'textContent',()=>StudioI18n.t(structure()==='scenes'?'video.plan_block_scenes_title_label':'video.plan_block_title_label'),true);
    StudioI18n.bind(el('videoPlanHelp'),'textContent',()=>StudioI18n.t(isS2V()?'video.plan_block_s2v_help':isLtx()?'video.plan_block_ltx_help':structure()==='scenes'?'video.plan_block_scenes_help':'video.plan_block_help'),true);
    if(!count)return;
    if(plan.prompts.length!==count){plan={prompts:[],key:''};renderPlan();}
  }
  // A picture the page made itself is a data URL; the page's security policy (connect-src 'self') forbids fetch() on it, so it is decoded here.
  const blobOf=value=>{
    const [head,body]=value.split(',');const raw=atob(body),bytes=new Uint8Array(raw.length);
    for(let index=0;index<raw.length;index++)bytes[index]=raw.charCodeAt(index);
    return new Blob([bytes],{type:(head.match(/^data:([^;]*)/)||[])[1]||'image/png'});
  };
  async function smallEnd(){
    try{return await toFrame(blobOf(end),1024);}catch{return null;}
  }
  async function smallFrame(){
    // Gemma only needs a thumbnail of the first frame; the full picture can be tens of megabytes as a data URL.
    try{return await toFrame(blobOf(source),1024);}catch{return null;}
  }
  // Gemma looks at a few pictures of the reference video; the video itself stays in the browser until the job starts.
  async function drivingFrames(count=3){
    if(!drivingUrl)return [];
    const video=document.createElement('video');video.muted=true;video.preload='auto';video.src=drivingUrl;
    await new Promise((resolve,reject)=>{video.onloadeddata=resolve;video.onerror=()=>reject(new Error(StudioI18n.t("video.driving_video_unreadable")));});
    const frames=[],length=video.duration||1;
    for(let index=0;index<count;index++){
      const seeked=new Promise(resolve=>{video.onseeked=resolve;});
      video.currentTime=Math.min(Math.max(0,length-0.05),length*(index+0.5)/count);await seeked;
      const scale=Math.min(1,768/Math.max(video.videoWidth,video.videoHeight)),canvas=document.createElement('canvas');
      canvas.width=Math.max(1,Math.round(video.videoWidth*scale));canvas.height=Math.max(1,Math.round(video.videoHeight*scale));
      canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);frames.push(canvas.toDataURL('image/jpeg',0.85));
    }
    video.removeAttribute('src');video.load();
    return frames;
  }
  const smallSubject=async()=>toFrame(blobOf(subject),1024);
  function drivingInfo(){
    const profile=profiles.find(p=>p.id===selectedEngine);
    StudioI18n.bind(el('videoDrivingInfo'),'textContent',()=>{
      if(!characterMode()||!drivingSeconds)return '';
      const shown=Math.min(drivingSeconds,Number(el('videoDuration').value)/(profile?.fps||24));
      return StudioI18n.t("video.driving_video_length_info",[drivingSeconds.toLocaleString(StudioI18n.locale,{maximumFractionDigits:1}),shown.toLocaleString(StudioI18n.locale,{maximumFractionDigits:1})]);
    },true);
  }
  async function buildPlan(){
    const count=segmentCount();
    if(!el('videoPrompt').value.trim()){
      const empty=new Error(StudioI18n.t('video.plan_enter_idea_first'));
      StudioI18n.bind(el('videoPlanStatus'),'textContent',()=>StudioI18n.remote(empty.message),true);
      throw empty;
    }
    el('videoPlanBuild').disabled=true;
    StudioI18n.bind(el('videoPlanStatus'),'textContent',()=>StudioI18n.t('video.plan_status_working'),true);
    try{
      const body={prompt:el('videoPrompt').value,segments:count,join:structure()==='scenes'?'cut':'continue'};
      if(['i2v','s2v'].includes(el('videoMode').value)&&source)body.source_image=await smallFrame();
      if(isLtx())body.sound=true;
      if(isS2V()){body.talking=true;if(el('videoSpeechMode').value==='clone')body.speech_text=el('videoSpeechText').value;}
      const answer=await api('plan',body);
      plan={prompts:answer.prompts,key:planKey()};renderPlan();
      StudioI18n.bind(el('videoPlanStatus'),'textContent',()=>StudioI18n.t('video.plan_status_ready',[answer.prompts.length]),true);
    }catch(error){
      StudioI18n.bind(el('videoPlanStatus'),'textContent',()=>StudioI18n.remote(error.message),true);
      throw error;
    }finally{el('videoPlanBuild').disabled=false;}
  }
  function addCharacter() {
    if(characters.length>=3)return;
    const person={name:'',image:null,voice_audio:null,speech_text:'',language:'Russian'};
    const card=document.createElement('div');card.className='video-character';card.tabIndex=0;
    const title=document.createElement('label');StudioI18n.bind(title, "textContent", () => (StudioI18n.t("video.add_character_name")), true);
    const name=document.createElement('input');name.type='text';name.maxLength=60;StudioI18n.bind(name, "placeholder", () => (StudioI18n.t("video.name_example_alice")), true);name.oninput=()=>person.name=name.value;
    const photoLabel=document.createElement('label');StudioI18n.bind(photoLabel, "textContent", () => (StudioI18n.t("video.photo_image_paste_ctrl_v")), true);
    const photo=document.createElement('input');photo.type='file';photo.accept='image/*';
    const preview=document.createElement('img');preview.className='video-frame';preview.hidden=true;StudioI18n.bind(preview, "alt", () => (StudioI18n.t("video.preview_character_image")), true);
    const load=async file=>{try{person.image=await toFrame(file,1024);preview.src=person.image;preview.hidden=false;}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
    photo.onchange=()=>{if(photo.files[0])load(photo.files[0]);};
    card.addEventListener('paste',event=>{const file=[...(event.clipboardData?.files || [])].find(f=>f.type.startsWith('image/'));if(file){event.preventDefault();load(file);}});
    const voiceLabel=document.createElement('label');StudioI18n.bind(voiceLabel, "textContent", () => (StudioI18n.t("video.voice_sample_optional")), true);
    const voice=document.createElement('input');voice.type='file';voice.accept='audio/*,.wav,.mp3,.flac,.m4a,.ogg';
    voice.onchange=()=>{const file=voice.files[0];person.voice_audio=null;if(!file)return;if(file.size>8*1024*1024){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.t("video.add_character_voice_sample_exceeds_8_mi_b")), true);return;}const reader=new FileReader();reader.onload=()=>{person.voice_audio=String(reader.result).replace(/^data:[^;]*;/,'data:audio/wav;');};reader.readAsDataURL(file);};
    const textLabel=document.createElement('label');StudioI18n.bind(textLabel, "textContent", () => (StudioI18n.t("video.add_character_dialogue_voice")), true);
    const text=document.createElement('textarea');text.rows=3;text.maxLength=1000;StudioI18n.bind(text, "placeholder", () => (StudioI18n.t("video.add_character_enter_what_should_say")), true);text.oninput=()=>person.speech_text=text.value;
    const language=document.createElement('select');StudioI18n.bind(language, "@aria-label", () => (StudioI18n.t("video.character_dialogue_language_field_label")), true);for(const [value,label] of [['Russian',StudioI18n.t("video.speech_language_russian_option_label")],['English','English'],['Auto',StudioI18n.t("video.add_character_detect_language")]]){const option=StudioI18n.option(() => (label), value);language.add(option);}language.onchange=()=>person.language=language.value;
    const remove=document.createElement('button');remove.type='button';remove.className='secondary';StudioI18n.bind(remove, "textContent", () => (StudioI18n.t("video.character_card_remove_button_label")), true);remove.onclick=()=>{characters.splice(characters.indexOf(person),1);card.remove();el('videoAddCharacter').disabled=false;};
    const prefix='videoCharacter-'+crypto.randomUUID();
    for(const [label,input,suffix] of [[title,name,'name'],[photoLabel,photo,'image'],[voiceLabel,voice,'voice'],[textLabel,text,'text']]){input.id=prefix+'-'+suffix;label.htmlFor=input.id;}
    card.append(title,name,photoLabel,photo,preview,voiceLabel,voice,textLabel,text,language,remove);
    characters.push(person);el('videoCharacters').append(card);el('videoAddCharacter').disabled=characters.length>=3;
  }
  const api = async (route, data) => {
    let response;
    try {response = await fetch('/api/video/' + route, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
    catch {throw new Error(StudioI18n.t("video.api_request_connect_studio_check_application_running_unavailable"));}
    let value;
    try {value = await response.json();} catch {throw new Error(StudioI18n.t("video.api_request_invalid_video_service_response_open_logs"));}
    if (!response.ok) throw new Error(StudioI18n.remote(value.error) || StudioI18n.remote(value.reason) || StudioI18n.t("video.api_request_video_unavailable"));
    return value;
  };
  function module(name) {
    if (el('musicWorkspace')) el('musicWorkspace').hidden = true;
    if (el('musicTab')) el('musicTab').setAttribute('aria-selected', 'false');
    if (el('textWorkspace')) el('textWorkspace').hidden = true;
    if (el('textTab')) el('textTab').setAttribute('aria-selected', 'false');
    el('imageWorkspace').hidden = name !== 'image'; el('videoWorkspace').hidden = name !== 'video';
    document.body.dataset.studioModule = name;
    el('imageTab').setAttribute('aria-selected', String(name === 'image'));
    el('videoTab').setAttribute('aria-selected', String(name === 'video'));
    if (name === 'video') refresh();
  }
  function header() {
    const profile = profiles.find(row => row.id === el('videoEngine').value);
    const descriptions = {t2v:StudioI18n.t("video.header_generate_video_text_description"), i2v:StudioI18n.t("video.header_animate_source_image"),
      first_last:StudioI18n.t("video.header_generate_video_between_first_last_frames"), characters:StudioI18n.t("video.header_generate_video_reference_characters"),
      s2v:StudioI18n.t("video.header_talking_character_image_speech"), animate:StudioI18n.t("video.header_animate_character"), replace:StudioI18n.t("video.header_replace_character")};
    StudioUI.setHeader('video', {model:profile?.name || 'Video',
      description:descriptions[el('videoMode').value] || StudioI18n.t("video.header_local_video_generation"),
      tone:profile ? (available ? 'good' : 'bad') : 'muted',
      status:profile ? (available ? StudioI18n.t("video.header_video_engine_model_files_available_gpu") : StudioI18n.remote(profile.reason) || StudioI18n.t("video.header_selected_model_unavailable")) : StudioI18n.t("video.header_checking_video_models")});
  }
  // Quality and power of the engine on a 0-10 scale (10 = the real world filmed by a good camera); the numbers come from the registry on the server.
  const ratingText=value=>Number(value).toLocaleString(StudioI18n.locale,{minimumFractionDigits:Number.isInteger(value)?0:1,maximumFractionDigits:1});
  function showRating(rating){
    el('videoRating').hidden=!rating;
    if(!rating)return;
    StudioI18n.bind(el('videoRatingOverall'),'textContent',()=>StudioI18n.t('video.rating_overall_score_label',[ratingText(rating.overall)]),true);
    StudioI18n.bind(el('videoRatingScale'),'textContent',()=>StudioI18n.t('video.rating_scale_anchor_hint'),true);
    const rows=[];
    for(const axis of ['picture','motion','consistency','sound','control']){
      const value=rating[axis];
      const term=document.createElement('dt'),meter=document.createElement('dd'),number=document.createElement('dd');
      StudioI18n.bind(term,'textContent',()=>StudioI18n.t(`video.rating_axis_${axis}_label`),true);
      if(value==null){
        meter.textContent='';
        StudioI18n.bind(number,'textContent',()=>StudioI18n.t('video.rating_axis_not_applicable'),true);
        number.style.gridColumn='2 / 4';number.style.textAlign='left';meter.hidden=true;
      }else{
        const bar=document.createElement('meter');bar.min=0;bar.max=10;bar.value=value;meter.appendChild(bar);number.textContent=ratingText(value);
      }
      rows.push(term,meter,number);
    }
    el('videoRatingAxes').replaceChildren(...rows);
  }
  function modes() {
    header();
    syncPlan();
    syncEnhance();
    const mode = el('videoMode').value;
    el('videoSourceBlock').hidden = !['i2v','first_last','s2v','a2v','animate','replace'].includes(mode);
    el('videoDrivingBlock').hidden = !characterMode();
    el('videoObjectsBlock').hidden = !(characterMode() && isScail());
    el('videoRelightRow').hidden = mode!=='replace';
    el('videoFitRow').hidden = !(characterMode() && (isAnimate() || mode==='replace'));   // SCAIL-2 "animate" keeps the photo's own scene
    el('videoEnhancedMotionLabel').hidden = el('videoEnhancedMotion').hidden = !(characterMode() && isAnimate());
    StudioI18n.bind(el('videoSourceLabel'),'textContent',()=>characterMode()?StudioI18n.t("video.source_person_photo_label"):StudioI18n.t("video.source_frame_input_field_label"),true);
    StudioI18n.bind(el('videoDrivingHelp'),'textContent',()=>isAnimate()?StudioI18n.t("video.driving_help_animate2"):mode==='replace'?StudioI18n.t("video.driving_help_replace"):StudioI18n.t("video.driving_help_scail_animate"),true);
    drivingInfo();
    el('videoAudioBlock').hidden = mode!=='a2v';
    // Audio-to-video and the first/last frame are one window of at most 121 frames: the 15/30/60 s continuations have no end to aim at
    // and need generated sound.
    const single=['a2v','first_last'].includes(mode) && isLtx();
    for(const option of el('videoDuration').options)option.hidden=single && Number(option.value)>121;
    if(single && el('videoDuration').selectedOptions[0]?.hidden)el('videoDuration').value='121';
    el('videoSpeechBlock').hidden=mode!=='s2v';
    el('videoCloneBlock').hidden=el('videoSpeechMode').value!=='clone';
    el('videoReadySpeechBlock').hidden=el('videoSpeechMode').value!=='audio';
    el('videoEndBlock').hidden = mode !== 'first_last';
    el('videoCharacterBlock').hidden=mode!=='characters';
    if(mode==='characters' && !characters.length)addCharacter();
    if(isProfiled()){for(const option of [...el('videoDuration').options,...el('videoQuality').options])option.disabled=false;return;}
    for(const option of el('videoDuration').options)option.disabled=!['t2v','characters'].includes(mode) && option.value!=='39';
    if(!['t2v','characters'].includes(mode))el('videoDuration').value='39';
    const previewOnly=mode!=='t2v' || el('videoDuration').value!=='39';
    for(const option of el('videoQuality').options)option.disabled=previewOnly && option.value!=='preview';
    if(previewOnly)el('videoQuality').value='preview';
  }
  function draft() {
    rememberEngine();
    try {localStorage.setItem('visualforge.video.draft.v1',JSON.stringify({enhance:el('videoEnhance').checked,speech:{mode:el('videoSpeechMode').value,text:el('videoSpeechText').value,language:el('videoSpeechLanguage').value},engine:selectedEngine,engineDrafts,prompt:el('videoPrompt').value,negative:el('videoNegative').value,seed:el('videoSeed').value,mode:el('videoMode').value,quality:el('videoQuality').value,frames:el('videoDuration').value}));} catch {}
  }
  async function toFrame(blob,maxSide=8192) {
    if (blob.size > 36*1024*1024) throw new Error(StudioI18n.t("video.to_frame_source_exceeds_36_mi_b"));
    const url = URL.createObjectURL(blob);
    try {
      const image = new Image(); image.src=url; await image.decode();
      if (image.naturalWidth > 8192 || image.naturalHeight > 8192) throw new Error(StudioI18n.t("video.to_frame_exceeds_8192_pixels_one_side"));
      const scale=Math.min(1,maxSide/Math.max(image.naturalWidth,image.naturalHeight));
      const canvas = document.createElement('canvas'); canvas.width=Math.max(1,Math.round(image.naturalWidth*scale)); canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
      const context=canvas.getContext('2d'); context.fillStyle='#eee'; context.fillRect(0,0,canvas.width,canvas.height); context.drawImage(image,0,0,canvas.width,canvas.height);
      return canvas.toDataURL('image/png');
    } finally {URL.revokeObjectURL(url);}
  }
  async function inputFrame(file, which) {
    if(!file)return;
    const value=await toFrame(file);
    if (which==='source') {source=value;el('videoSourcePreview').src=value;el('videoSourcePreview').hidden=false;}
    else {end=value;el('videoEndPreview').src=value;el('videoEndPreview').hidden=false;}
  }
  async function history() {
    const data=await api('history'); el('videoHistory').replaceChildren();
    for (const row of data.entries.slice(0,20)) {
      const card=document.createElement('div');card.className='video-history-card';
      const title=document.createElement('p');StudioI18n.bind(title, "textContent", () => (`${engineName(row.engine)} · ${({t2v:StudioI18n.t("video.history_text_to_video_mode_label"),i2v:StudioI18n.t("video.history_image_to_video_mode_label"),first_last:StudioI18n.t("video.history_between_frames"),characters:StudioI18n.t("video.history_character_reference_mode_label"),s2v:StudioI18n.t("video.history_talking_character"),a2v:StudioI18n.t("video.history_audio_to_video")})[row.mode] || row.mode} · ${({preview:StudioI18n.t("video.generation_preview_quality_option"),standard:StudioI18n.t("video.generation_standard_quality_option"),high:StudioI18n.t("video.generation_high_quality_option")})[row.quality] || row.quality} · Seed ${row.seed} · ${row.state === 'done' ? StudioI18n.t("video.generation_completed_status_label") : row.state === 'cancelled' ? StudioI18n.t("video.generation_cancelled_status_label") : StudioI18n.t("video.generation_failed_status_label")}`), true);
      const prompt=document.createElement('p');StudioI18n.bind(prompt, "textContent", () => ((row.mix_summary?StudioI18n.remote(row.mix_summary)+' · ':'')+row.original_prompt), false);
      const reuse=document.createElement('button');reuse.type='button';reuse.className='secondary';StudioI18n.bind(reuse, "textContent", () => (StudioI18n.t("video.reuse_restore_prompt_seed")), true);reuse.onclick=()=>{el('videoPrompt').value=row.original_prompt;el('videoNegative').value=row.negative_prompt || '';el('videoSeed').value=row.seed;draft();};
      card.append(title,prompt,reuse);
      if(row.state==='done') {const play=document.createElement('button');play.type='button';play.className='secondary';StudioI18n.bind(play, "textContent", () => (StudioI18n.t("video.play_open_video")), true);play.onclick=()=>show(row);card.append(play);}
      card.append(StudioUI.deleteGenerationButton('Video', row.id, async () => {
        if (last?.id === row.id) {
          last=null; el('videoPlayer').pause(); el('videoPlayer').removeAttribute('src'); el('videoPlayer').load(); el('videoPlayer').hidden=true;
          el('videoDownload').removeAttribute('href'); el('videoDownload').hidden=true;
          StudioI18n.bind(el('videoResultMeta'), "textContent", () => (''), true); StudioI18n.bind(el('videoSavedPath'), "textContent", () => (''), true);
          exportSourceId=''; ++previewSeq; ++mixSeq; el('videoExportBlock').hidden=true; el('videoMixBlock').hidden=true;
        }
        StudioI18n.bind(el('videoLogs'), "textContent", () => (StudioI18n.t("video.logs_generation_deleted")), false);
        await history();
      }, () => {
        if (last?.id === row.id) {el('videoPlayer').pause(); el('videoPlayer').removeAttribute('src'); el('videoPlayer').load();}
      }));
      if (row.generation) card.append(StudioUI.generationInfo(row.generation));
      el('videoHistory').append(card);
    }
  }
  function show(row) {
    last=row;const url='/api/video/result?id='+encodeURIComponent(row.id);
    el('videoPlayer').src=url;el('videoPlayer').hidden=false;
    el('videoDownload').href=url;el('videoDownload').download=row.filename || 'Generated Video.mp4';el('videoDownload').hidden=false;
    StudioI18n.bind(el('videoResultMeta'), "textContent", () => (row.metadata ? StudioI18n.t("video.meta_frames_s_seed", [engineName(row.engine), row.metadata.width, row.metadata.height, row.metadata.frames, row.metadata.duration, row.metadata.has_audio ? StudioI18n.t("video.meta_audio_stream_present") : StudioI18n.t("video.meta_no_audio_stream"), row.seed]) : ''), true);
    StudioI18n.bind(el('videoSavedPath'), "textContent", () => (row.saved_path ? StudioI18n.t("video.output_saved_path_information_prefix")+row.saved_path : ''), true);
    exportOpen(row);mixOpen(row);
  }
  async function logs(id) {if(!id){StudioI18n.bind(el('videoLogs'), "textContent", () => (StudioI18n.t("video.logs_task_has_not_started_yet_error")), false);return;}try {StudioI18n.value(el('videoLogs'), "textContent", ((await api('logs?id='+encodeURIComponent(id))).text), false);} catch(error) {StudioI18n.bind(el('videoLogs'), "textContent", () => (StudioI18n.remote(error.message)), false);}}
  async function poll() {
    if(!active || polling)return;
    polling=true;
    try {
      const row=await api('status?id='+encodeURIComponent(active.id));
      StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.t("video.poll_elapsed_s", [engineName(row.engine), row.continuation_part ? StudioI18n.t("video.generation_continuation_segment_prefix")+row.continuation_part+' / '+row.continuation_parts+' · ' : '', row.scene ? StudioI18n.t("video.generation_scene_index_prefix")+row.scene+' / '+row.scenes+' · ' : '', StudioI18n.remote(row.phase), row.elapsed_sec])), true);
      if (['done','error','cancelled'].includes(row.state)) {
        clearInterval(timer);timer=null;
        active=null;
        el('videoGenerate').disabled=!available;el('videoCancel').disabled=true;
        if(typeof engineReady==='function')el('generate').disabled=!engineReady();
        if(row.state==='done')show(row);
        if(row.state!=='cancelled')StudioUI.notify({module:'Video',id:row.id,error:row.state==='error',
          message:StudioI18n.remote(row.error) || StudioI18n.t("video.poll_generation_assembly_completed_video_saved"),folder:row.state==='done'?StudioUI.parentFolder(row.saved_path):'',
          onLogs:() => StudioUI.showLogs('Video',row.id,logs)});
        if(StudioI18n.remote(row.error))StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(row.error)), true);
        await Promise.all([history(),logs(row.id)]);
      }
    } catch(error) {StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);}
    finally {polling=false;}
  }
  async function refresh() {
    try {
      const status=await api('availability');
      if(profiles.length)rememberEngine();
      profiles=status.engines || [];exportInit(status.delivery);
      el('videoEngine').replaceChildren(...profiles.map(p=>StudioI18n.option(() => (StudioUI.modelLabel(p.id,p.name)+(p.rating?` · ${StudioI18n.t('video.rating_option_suffix_label',[ratingText(p.rating.overall)])}`:'')), p.id)));
      el('videoEngine').value=profiles.some(p=>p.id===selectedEngine)?selectedEngine:profiles[0]?.id;
      el('videoEngine').disabled=false;configureEngine();
      StudioI18n.bind(el('videoVoiceAvailability'), "textContent", () => (status.voice_available ? StudioI18n.t("video.voice_availability_local_cloning_qwen3_tts_ready") : StudioI18n.t("video.voice_availability_cloning_module_not_installed_yet")), true);
      el('videoGenerate').disabled=!available || !!active;
      if(status.available) {
        el('videoSaveDir').value=(await api('settings')).save_dir;await history();await prompts();
        const exporting=(await api('export-status')).export;
        if(exporting && !exportJob && !mixJob)(exporting.kind==='mix'?attachMix:attachExport)(exporting);
        const running=await api('active');
        if(running.job && !active) {
          active=running.job;el('videoGenerate').disabled=true;el('videoCancel').disabled=false;el('generate').disabled=true;
          if(!timer)timer=setInterval(poll,1000);poll();
        }
      }
    } catch(error) {StudioI18n.bind(el('videoAvailability'), "textContent", () => (StudioI18n.remote(error.message)), true);available=false;el('videoGenerate').disabled=true;StudioUI.setHeader('video', {tone:'bad',status:StudioI18n.remote(error.message)});}
  }
  async function prompts() {
    const data=await api('prompts');el('videoPrompts').replaceChildren();
    for(const entry of data.entries) {
      const card=document.createElement('div');card.className='video-history-card';const text=document.createElement('p');StudioI18n.bind(text, "textContent", () => (entry.prompt), false);
      const use=document.createElement('button');use.type='button';use.className='secondary';StudioI18n.bind(use, "textContent", () => (StudioI18n.t("video.use_append_prompt")), true);use.onclick=()=>{el('videoPrompt').value=[el('videoPrompt').value,entry.prompt].filter(Boolean).join('\n\n');draft();};
      const remove=document.createElement('button');remove.type='button';remove.className='secondary';StudioI18n.bind(remove, "textContent", () => (StudioI18n.t("video.generation_delete_button_label")), true);remove.onclick=async()=>{await api('prompts',{action:'delete',id:entry.id});await prompts();};card.append(text,use,remove);el('videoPrompts').append(card);
    }
  }
  // Export for social networks. The plan (frame placement, notes) is always computed by the server with the same code the export uses.
  let exportJob=null, exportTimer=null, exportPolling=false, exportSourceId=null, exportPlan=null, previewSeq=0, thumb=null;
  const EXPORT_KEY='visualforge.video.export.v1';
  const fmtNum=(n,digits=2)=>Number(n).toLocaleString(StudioI18n.locale,{maximumFractionDigits:digits});
  const fmtFps=value=>{const [a,b=1]=String(value).split('/').map(Number);return fmtNum(a/b);};
  const listItem=(text,className)=>{const item=document.createElement('li');StudioI18n.bind(item, "textContent", () => (text), false);if(className)item.className=className;return item;};
  function exportDraft(){try{localStorage.setItem(EXPORT_KEY,JSON.stringify({preset:el('videoExportPreset').value,audio:el('videoExportAudio').value}));}catch{}}
  function exportInit(info){
    let saved={};try{saved=JSON.parse(localStorage.getItem(EXPORT_KEY) || '{}');}catch{}
    const keep=el('videoExportPreset').value || saved.preset;
    el('videoExportPreset').replaceChildren(...(info?.presets || []).map(p=>StudioI18n.option(() => (`${p.label} · ${p.width} × ${p.height}`), p.id)));
    if([...el('videoExportPreset').options].some(o=>o.value===keep))el('videoExportPreset').value=keep;
    if(!el('videoExportAudio').dataset.restored){if(['keep','fill','require'].includes(saved.audio))el('videoExportAudio').value=saved.audio;el('videoExportAudio').dataset.restored='1';}
  }
  function drawExport(plan){
    const canvas=el('videoExportCanvas'),{width:tw,height:th}=plan.target,scale=Math.min(240/tw,300/th);
    canvas.width=Math.max(2,Math.round(tw*scale));canvas.height=Math.max(2,Math.round(th*scale));
    const g=canvas.getContext('2d'),p=plan.picture,x=p.x*scale,y=p.y*scale,w=p.width*scale,h=p.height*scale;
    g.fillStyle='#000';g.fillRect(0,0,canvas.width,canvas.height);
    // A separate, muted video seeked to a representative moment: the main player may not have painted its first frame yet.
    if(thumb && thumb.readyState>=2 && !thumb.seeking){try{g.drawImage(thumb,x,y,w,h);}catch{}}
    else{g.fillStyle='#2b3550';g.fillRect(x,y,w,h);}
    g.strokeStyle='#7968f4';g.lineWidth=1;g.strokeRect(x+.5,y+.5,Math.max(1,w-1),Math.max(1,h-1));
  }
  function exportFacts(plan){
    const p=plan.picture,s=plan.source,t=plan.target;
    const bars=[p.y>0?StudioI18n.t("video.export_facts_px_top_bottom", [p.y]):'',p.x>0?StudioI18n.t("video.export_facts_px_left_right", [p.x]):''].filter(Boolean);
    return [
      StudioI18n.t("video.export_facts_source_fps_s", [s.width, s.height, fmtFps(s.fps), fmtNum(s.duration,3)]),
      StudioI18n.t("video.export_facts_output_fps_mp4_h_264", [t.width, t.height, plan.aspect, t.fps]),
      StudioI18n.t("video.export_facts_picture_within_canvas", [p.width, p.height]) + (bars.length?StudioI18n.t("video.export_facts_black_bars", [bars.join(', ')]):StudioI18n.t("video.export_facts_no_bars")),
      p.scaled_up?StudioI18n.t("video.export_facts_scale_image_enlarged_without_adding_detail", [fmtNum(p.scale_factor)]):p.scale_factor<1?StudioI18n.t("video.export_facts_scale_image_reduced", [fmtNum(p.scale_factor)]):StudioI18n.t("video.export_facts_scale_unchanged"),
      plan.frame_rate_conversion==='none'?StudioI18n.t("video.export_facts_frame_rate_unchanged"):StudioI18n.t("video.export_facts_frame_rate_fps_frames_duplicated_skipped", [fmtFps(s.fps), t.fps]),
      ({source:StudioI18n.t("video.export_facts_audio_clip"),silence:StudioI18n.t("video.export_facts_audio_silent_track_added"),none:StudioI18n.t("video.export_facts_output_have_no_audio")})[plan.audio_origin]
    ];
  }
  async function updateExportPreview(){
    if(!exportSourceId)return;
    const seq=++previewSeq;el('videoExportStart').disabled=true;
    try{
      const plan=await api(`export-preview?id=${encodeURIComponent(exportSourceId)}&preset=${encodeURIComponent(el('videoExportPreset').value)}&audio=${encodeURIComponent(el('videoExportAudio').value)}`);
      if(seq!==previewSeq)return;
      exportPlan=plan;drawExport(plan);
      el('videoExportFacts').replaceChildren(...exportFacts(plan).map(text=>listItem(text)),...(plan.blocked?[listItem(plan.blocked,'export-blocked')]:[]));
      el('videoExportStart').disabled=!!plan.blocked || !!exportJob || !!mixJob;
    }catch(error){
      if(seq!==previewSeq)return;
      exportPlan=null;el('videoExportFacts').replaceChildren(listItem(StudioI18n.remote(error.message),'export-blocked'));
    }
  }
  function loadThumb(id){
    if(thumb){thumb.removeAttribute('src');thumb.load();}
    thumb=document.createElement('video');thumb.muted=true;thumb.preload='auto';thumb.src='/api/video/result?id='+encodeURIComponent(id);
    thumb.addEventListener('loadedmetadata',()=>{thumb.currentTime=Math.min((thumb.duration || 0)/3,2);},{once:true});
    thumb.addEventListener('seeked',()=>{if(exportPlan)drawExport(exportPlan);});
  }
  function exportOpen(row){exportSourceId=row.id;el('videoExportBlock').hidden=false;loadThumb(row.id);updateExportPreview();}
  function attachExport(row){
    exportJob=row;el('videoExportStart').disabled=true;el('videoExportCancel').disabled=false;el('videoExportProgress').hidden=false;
    if(!exportTimer)exportTimer=setInterval(pollExport,700);pollExport();
  }
  async function pollExport(){
    if(!exportJob || exportPolling)return;
    exportPolling=true;
    try{
      const row=await api('export-status?id='+encodeURIComponent(exportJob.id));
      exportJob=row;el('videoExportProgress').value=row.progress || 0;
      StudioI18n.bind(el('videoExportStatus'), "textContent", () => (StudioI18n.t("video.export_elapsed_s", [StudioI18n.remote(row.label), StudioI18n.remote(row.phase), row.elapsed_sec])), true);
      if(row.state!=='running'){
        clearInterval(exportTimer);exportTimer=null;exportJob=null;
        el('videoExportCancel').disabled=true;el('videoExportProgress').hidden=true;
        if(row.state==='done')StudioI18n.bind(el('videoExportStatus'), "textContent", () => (StudioI18n.t("video.export_completed_saved_path_template", [row.saved_path])), true);
        else if(row.state==='cancelled')StudioI18n.bind(el('videoExportStatus'), "textContent", () => (StudioI18n.t("video.export_cancelled_partial_file_deleted")), true);
        else StudioI18n.bind(el('videoExportStatus'), "textContent", () => (StudioI18n.remote(row.error) || StudioI18n.t("video.export_execution_failed_error")), true);
        if(row.state!=='cancelled')StudioUI.notify({module:'Video',id:row.id,error:row.state==='error',
          message:row.state==='error'?StudioI18n.remote(row.error):StudioI18n.t("video.poll_export_completed", [StudioI18n.remote(row.label), row.filename]),folder:row.state==='done'?StudioUI.parentFolder(row.saved_path):''});
        updateExportPreview();updateMixPreview();
      }
    }catch(error){StudioI18n.bind(el('videoExportStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);}
    finally{exportPolling=false;}
  }
  el('videoExportStart').onclick=async()=>{
    if(!exportSourceId || exportJob || mixJob)return;
    el('videoExportStart').disabled=true;el('videoMixStart').disabled=true;StudioI18n.bind(el('videoExportStatus'), "textContent", () => (StudioI18n.t("video.export_execution_starting_status")), true);el('videoExportProgress').value=0;
    try{attachExport(await api('export',{id:exportSourceId,preset:el('videoExportPreset').value,audio:el('videoExportAudio').value}));}
    catch(error){StudioI18n.bind(el('videoExportStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);StudioUI.notify({module:'Video',error:true,message:StudioI18n.remote(error.message)});updateExportPreview();}
  };
  el('videoExportCancel').onclick=async()=>{
    if(!exportJob)return;el('videoExportCancel').disabled=true;
    try{await api('export-cancel',{id:exportJob.id,cancel_token:exportJob.cancel_token});}catch(error){StudioI18n.bind(el('videoExportStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);el('videoExportCancel').disabled=false;}
  };
  el('videoExportPreset').onchange=()=>{exportDraft();updateExportPreview();};
  el('videoExportAudio').onchange=()=>{exportDraft();updateExportPreview();};
  // Music and sound. The server plans the mix with the same code that performs it; the browser only draws the plan.
  let mixJob=null, mixTimer=null, mixPolling=false, mixPlan=null, mixSeq=0, mixDebounce=null, musicList=[], clipHasAudio=false;
  const mixTracks=[];
  const mixNumber=(input,fallback)=>{const value=Number(input.value);return input.value.trim()!=='' && Number.isFinite(value)?value:fallback;};
  const scheduleMix=()=>{clearTimeout(mixDebounce);mixDebounce=setTimeout(updateMixPreview,250);};
  async function loadMusic(){
    try{
      const data=await (await fetch('/api/music/history')).json();
      musicList=(data.entries || []).filter(e=>e.state==='done' && e.saved_path).map(e=>({id:e.id,label:StudioI18n.t("video.music_library_track_duration_template", [String(e.caption || 'Music').slice(0,45), fmtNum(e.metadata?.duration || e.duration || 0,1)])}));
    }catch{musicList=[];}
    for(const track of mixTracks)track.fill();
    scheduleMix();
  }
  function addMixTrack(){
    if(mixTracks.length>=3)return;
    const uid='mix-'+crypto.randomUUID(),card=document.createElement('div');card.className='video-character mix-track';
    const field=(text,id,attrs)=>{const label=document.createElement('label');label.htmlFor=uid+'-'+id;StudioI18n.bind(label, "textContent", () => (text), false);const input=document.createElement('input');input.id=uid+'-'+id;Object.assign(input,attrs);input.oninput=input.onchange=scheduleMix;return {label,input};};
    const musicLabel=document.createElement('label');musicLabel.htmlFor=uid+'-music';StudioI18n.bind(musicLabel, "textContent", () => (StudioI18n.t("video.music_track_section", [mixTracks.length+1])), true);
    const select=document.createElement('select');select.id=uid+'-music';select.onchange=scheduleMix;
    const start=field(StudioI18n.t("video.add_mix_track_start_s"),'start',{type:'number',min:'0',max:'3600',step:'0.1',value:'0'});
    const gain=field(StudioI18n.t("video.add_mix_track_volume_d_b"),'gain',{type:'number',min:'-40',max:'12',step:'1',value:clipHasAudio?'-12':'-6'});
    const fadeIn=field(StudioI18n.t("video.add_mix_track_fade_s"),'fadein',{type:'number',min:'0',max:'30',step:'0.1',value:'0.3'});
    const fadeOut=field(StudioI18n.t("video.add_mix_track_fade_out_s"),'fadeout',{type:'number',min:'0',max:'30',step:'0.1',value:'1.2'});
    const loop=field(StudioI18n.t("video.add_mix_track_loop_until_video_ends"),'loop',{type:'checkbox'});loop.label.className='mix-check';loop.label.prepend(loop.input);
    const remove=document.createElement('button');remove.type='button';remove.className='secondary';StudioI18n.bind(remove, "textContent", () => (StudioI18n.t("video.mixer_remove_music_track_button_label")), true);
    const row=document.createElement('div');row.className='mix-row';row.append(...[start,gain,fadeIn,fadeOut].flatMap(f=>[f.label,f.input]));
    const track={
      fill(){const keep=select.value;select.replaceChildren(StudioI18n.option(() => (musicList.length?StudioI18n.t("video.add_mix_track_select_music"):StudioI18n.t("video.add_mix_track_no_music_available_create")), ''),...musicList.map(m=>StudioI18n.option(() => (m.label), m.id)));if(musicList.some(m=>m.id===keep))select.value=keep;},
      read(){return {music_id:select.value,label:select.selectedOptions[0]?.textContent.slice(0,60) || 'Music',start:mixNumber(start.input,0),gain_db:mixNumber(gain.input,-12),fade_in:mixNumber(fadeIn.input,0),fade_out:mixNumber(fadeOut.input,0),loop:loop.input.checked};}
    };
    remove.onclick=()=>{mixTracks.splice(mixTracks.indexOf(track),1);card.remove();el('videoMixAdd').disabled=false;scheduleMix();};
    card.append(musicLabel,select,row,loop.label,remove);el('videoMixTracks').append(card);mixTracks.push(track);track.fill();el('videoMixAdd').disabled=mixTracks.length>=3;
    if(mixTracks.length===1 && musicList.length)select.value=musicList[0].id;
    scheduleMix();
  }
  function mixRequest(){
    if(!exportSourceId || !mixTracks.length)return null;
    const tracks=mixTracks.map(t=>t.read());
    if(tracks.some(t=>!t.music_id))return null;
    return {id:exportSourceId,native:{mode:el('videoMixNative').value,gain_db:mixNumber(el('videoMixNativeGain'),0)},tracks,ducking:el('videoMixDucking').checked};
  }
  function drawMix(plan){
    const canvas=el('videoMixCanvas'),lanes=plan.lanes,rowH=26,top=6,width=480,height=top+lanes.length*rowH+22;
    canvas.width=width;canvas.height=height;
    const g=canvas.getContext('2d'),scale=(width-12)/Math.max(plan.duration,.1),x=t=>6+t*scale,colors={video:'#3b4256',native:'#2f7fd0',music:'#7968f4'};
    g.fillStyle='#0b1018';g.fillRect(0,0,width,height);g.font='12px sans-serif';g.textBaseline='middle';
    lanes.forEach((lane,i)=>{
      const y=top+i*rowH,h=rowH-6,x0=x(lane.start),w=Math.max(2,(lane.end-lane.start)*scale);
      g.globalAlpha=lane.kind==='music'?Math.min(1,Math.max(.4,1+lane.gain_db/30)):1;g.fillStyle=colors[lane.kind];g.fillRect(x0,y,w,h);g.globalAlpha=1;
      if(lane.kind==='music'){
        g.fillStyle='rgba(0,0,0,.5)';
        if(lane.fade_in){g.beginPath();g.moveTo(x0,y);g.lineTo(x0+lane.fade_in*scale,y);g.lineTo(x0,y+h);g.fill();}
        if(lane.fade_out){g.beginPath();g.moveTo(x0+w,y);g.lineTo(x0+w-lane.fade_out*scale,y);g.lineTo(x0+w,y+h);g.fill();}
      }
      g.fillStyle='#fff';g.fillText(lane.gain_db===null?lane.label:StudioI18n.t("video.draw_mix_d_b", [lane.label, fmtNum(lane.gain_db,1)]),x0+6,y+h/2,Math.max(20,w-10));
    });
    const step=plan.duration<=12?1:plan.duration<=40?5:10,axis=top+lanes.length*rowH;
    g.fillStyle='#9fb0c8';g.strokeStyle='#526077';g.textBaseline='top';
    for(let t=0;t<=plan.duration+1e-6;t+=step){g.beginPath();g.moveTo(x(t),axis-4);g.lineTo(x(t),axis+2);g.stroke();g.fillText(StudioI18n.t("video.duration_seconds_template", [t]),x(t)-8,axis+4);}
  }
  async function updateMixPreview(){
    const seq=++mixSeq,body=mixRequest();el('videoMixStart').disabled=true;
    if(!body){
      mixPlan=null;el('videoMixFacts').replaceChildren(listItem(mixTracks.length?StudioI18n.t("video.update_mix_preview_select_music_every_track"):StudioI18n.t("video.update_mix_preview_add_least_one_music_track")));
      el('videoMixCanvas').width=1;el('videoMixCanvas').height=1;return;
    }
    try{
      const plan=await api('mix-preview',body);
      if(seq!==mixSeq)return;
      mixPlan=plan;drawMix(plan);
      el('videoMixFacts').replaceChildren(listItem(StudioI18n.t("video.update_mix_preview_clip_duration_s", [fmtNum(plan.duration,2), plan.native_in?StudioI18n.t("video.update_mix_preview_clip_audio_preserved"):StudioI18n.t("video.update_mix_preview_clip_audio_removed")])),...plan.notes.map(t=>listItem(t)),...(plan.blocked?[listItem(plan.blocked,'export-blocked')]:[]));
      el('videoMixStart').disabled=!!plan.blocked || !!mixJob || !!exportJob;
    }catch(error){if(seq!==mixSeq)return;mixPlan=null;el('videoMixFacts').replaceChildren(listItem(StudioI18n.remote(error.message),'export-blocked'));}
  }
  function mixOpen(row){
    clipHasAudio=!!row.metadata?.has_audio;
    el('videoMixBlock').hidden=false;el('videoMixNativeBlock').hidden=!clipHasAudio;
    el('videoMixDucking').disabled=!clipHasAudio;if(!clipHasAudio)el('videoMixDucking').checked=false;
    StudioI18n.bind(el('videoMixDucking'), "title", () => (clipHasAudio?'':StudioI18n.t("video.mix_ducking_clip_has_no_original_audio_duck")), true);
    loadMusic();
  }
  function attachMix(row){
    mixJob=row;el('videoMixStart').disabled=true;el('videoExportStart').disabled=true;el('videoMixCancel').disabled=false;el('videoMixProgress').hidden=false;
    if(!mixTimer)mixTimer=setInterval(pollMix,600);pollMix();
  }
  async function pollMix(){
    if(!mixJob || mixPolling)return;
    mixPolling=true;
    try{
      const row=await api('export-status?id='+encodeURIComponent(mixJob.id));
      mixJob=row;el('videoMixProgress').value=row.progress || 0;
      StudioI18n.bind(el('videoMixStatus'), "textContent", () => (StudioI18n.t("video.mix_elapsed_s", [StudioI18n.remote(row.phase), row.elapsed_sec])), true);
      if(row.state!=='running'){
        clearInterval(mixTimer);mixTimer=null;mixJob=null;el('videoMixCancel').disabled=true;el('videoMixProgress').hidden=true;
        if(row.state==='done')StudioI18n.bind(el('videoMixStatus'), "textContent", () => (StudioI18n.t("video.export_completed_saved_path_template", [row.saved_path])), true);
        else if(row.state==='cancelled')StudioI18n.bind(el('videoMixStatus'), "textContent", () => (StudioI18n.t("video.mix_mixing_cancelled_partial_file_deleted")), true);
        else StudioI18n.bind(el('videoMixStatus'), "textContent", () => (StudioI18n.remote(row.error) || StudioI18n.t("video.mix_mixing_failed")), true);
        if(row.state!=='cancelled')StudioUI.notify({module:'Video',id:row.id,error:row.state==='error',
          message:row.state==='error'?StudioI18n.remote(row.error):StudioI18n.t("video.poll_mix_music_added", [row.filename]),folder:row.state==='done'?StudioUI.parentFolder(row.saved_path):''});
        if(row.state==='done' && row.result_id){await history();show(await api('status?id='+encodeURIComponent(row.result_id)));}
        else updateMixPreview();
        updateExportPreview();
      }
    }catch(error){StudioI18n.bind(el('videoMixStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);}
    finally{mixPolling=false;}
  }
  el('videoMixStart').onclick=async()=>{
    const body=mixRequest();if(!body || mixJob || exportJob)return;
    el('videoMixStart').disabled=true;StudioI18n.bind(el('videoMixStatus'), "textContent", () => (StudioI18n.t("video.mix_starting_mixing")), true);el('videoMixProgress').value=0;
    try{attachMix(await api('mix',body));}
    catch(error){StudioI18n.bind(el('videoMixStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);StudioUI.notify({module:'Video',error:true,message:StudioI18n.remote(error.message)});updateMixPreview();}
  };
  el('videoMixCancel').onclick=async()=>{
    if(!mixJob)return;el('videoMixCancel').disabled=true;
    try{await api('export-cancel',{id:mixJob.id,cancel_token:mixJob.cancel_token});}catch(error){StudioI18n.bind(el('videoMixStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);el('videoMixCancel').disabled=false;}
  };
  el('videoMixAdd').onclick=addMixTrack;el('videoMixRefresh').onclick=loadMusic;
  el('videoMixNative').onchange=el('videoMixNativeGain').oninput=el('videoMixDucking').onchange=scheduleMix;
  el('imageTab').onclick=()=>module('image');el('videoTab').onclick=()=>module('video');
  el('videoEngine').onchange=()=>{rememberEngine();configureEngine();draft();if(!available)window.repairStudioModel?.('video',selectedEngine);};
  el('videoResolution').onchange=draft;
  el('videoMode').onchange=()=>{modes();draft();};
  el('videoAddCharacter').onclick=addCharacter;
  el('videoDuration').onchange=()=>{suggestScenes();modes();draft();};
  el('videoStructure').onchange=()=>{const profile=profiles.find(p=>p.id===selectedEngine);if(profile)rebuildDurations(profile);suggestScenes();modes();draft();};
  el('videoSceneCount').oninput=()=>{sceneTouched=true;modes();draft();};
  el('videoPrompt').oninput=draft;el('videoNegative').oninput=draft;el('videoSeed').oninput=draft;el('videoQuality').onchange=draft;
  async function readSpeech(file){
    if(!file)return null;if(file.size>8*1024*1024)throw new Error(StudioI18n.t("video.read_speech_audio_file_exceeds_8_mi_b"));
    return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).replace(/^data:[^;]*;/,'data:audio/wav;'));reader.onerror=()=>reject(new Error(StudioI18n.t("video.reader_read_audio_file_failed")));reader.readAsDataURL(file);});
  }
  el('videoVoiceSample').onchange=async()=>{speechReference=null;try{speechReference=await readSpeech(el('videoVoiceSample').files[0]);}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('videoAudioFile').onchange=async()=>{recordingAudio=null;try{recordingAudio=await readSpeech(el('videoAudioFile').files[0]);}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('videoReadySpeech').onchange=async()=>{speechAudio=null;speechAudioSeconds=0;try{speechAudio=await readSpeech(el('videoReadySpeech').files[0]);if(speechAudio)speechAudioSeconds=await measureAudio(el('videoReadySpeech').files[0]);modes();}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('videoSpeechMode').onchange=()=>{modes();draft();};el('videoSpeechText').oninput=()=>{modes();draft();};el('videoSpeechLanguage').onchange=draft;
  el('videoSource').onchange=async()=>{try{await inputFrame(el('videoSource').files[0],'source');}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('videoDriving').onchange=async()=>{
    driving=null;drivingSeconds=0;if(drivingUrl)URL.revokeObjectURL(drivingUrl);drivingUrl=null;el('videoDrivingPreview').hidden=true;
    const file=el('videoDriving').files[0];if(!file){drivingInfo();return;}
    if(file.size>64*1024*1024){
      StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.t("video.generation_driving_video_too_large")), true);
      return;
    }
    try{
      driving=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).replace(/^data:[^;]*;/,'data:video/mp4;'));reader.onerror=()=>reject(new Error(StudioI18n.t("video.driving_video_unreadable")));reader.readAsDataURL(file);});
      drivingUrl=URL.createObjectURL(file);el('videoDrivingPreview').src=drivingUrl;el('videoDrivingPreview').hidden=false;
      drivingSeconds=await new Promise(resolve=>{const probe=document.createElement('video');probe.preload='metadata';probe.onloadedmetadata=()=>resolve(Number.isFinite(probe.duration)?probe.duration:0);probe.onerror=()=>resolve(0);probe.src=drivingUrl;});
      drivingInfo();
    }catch(e){driving=null;StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}
  };
  el('videoSubject').onchange=async()=>{subject=null;el('videoSubjectPreview').hidden=true;const file=el('videoSubject').files[0];if(!file)return;try{subject=await toFrame(file);el('videoSubjectPreview').src=subject;el('videoSubjectPreview').hidden=false;}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('videoEnd').onchange=async()=>{try{await inputFrame(el('videoEnd').files[0],'end');}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  function generationRequest() {
    const data={engine:el('videoEngine').value,mode:el('videoMode').value,prompt:el('videoPrompt').value,quality:el('videoQuality').value,seed:el('videoSeed').value,width:Number(el('videoResolution').value.split('x')[0]),height:Number(el('videoResolution').value.split('x')[1]),frames:Number(el('videoDuration').value),fps:profiles.find(p=>p.id===el('videoEngine').value)?.fps || 24};
    if (!el('videoNegativeBlock').hidden && el('videoNegative').value.trim()) data.negative_prompt=el('videoNegative').value;
    if (isS2V() && data.mode==='s2v') data.structure=structure();
    if (longEngine() && structure()==='scenes') {
      const layout=sceneLayout();
      if (!layout) throw new Error(StudioI18n.t("video.scene_info_invalid"));
      if (layout.tooFew) throw new Error(StudioI18n.t("video.scene_info_too_few",[layout.minimum]));
      data.scenes=layout.n;
    }
    if (segmentCount()) data.segment_prompts=plan.prompts.slice();
    if (['i2v','first_last','s2v','animate','replace'].includes(data.mode)) {
      if (!source) throw new Error(StudioI18n.t("video.generation_add_source_frame"));
      data.source_image=source;
    }
    if (characterMode()) {
      if (!driving) throw new Error(StudioI18n.t("video.generation_add_driving_video"));
      data.driving_video=driving;
      data.keep_audio=el('videoKeepAudio').checked;
      if (isAnimate() || data.mode==='replace') data.fit_reference=el('videoFitReference').checked;
      if (isScail()) {
        data.reference_object=el('videoReferenceObject').value.trim() || 'human';
        data.driving_object=el('videoDrivingObject').value.trim() || 'human';
        if (data.mode==='replace') data.relight=el('videoRelight').checked;
        if (subject) data.subjects=[{image:subject,reference_object:el('videoSubjectReference').value.trim() || 'car',driving_object:el('videoSubjectDriving').value.trim() || 'truck'}];
      }
    }
    if (data.mode==='s2v') {
      data.speech_mode=el('videoSpeechMode').value;
      if (data.speech_mode==='clone') {
        if (!speechReference) throw new Error(StudioI18n.t("video.generation_add_voice_sample"));
        if (!el('videoSpeechText').value.trim()) throw new Error(StudioI18n.t("video.generation_enter_short_line_dialogue"));
        data.voice_audio=speechReference;
        data.speech_text=el('videoSpeechText').value;
        data.language=el('videoSpeechLanguage').value;
      } else {
        if (!speechAudio) throw new Error(StudioI18n.t("video.generation_add_speech_recording"));
        data.source_audio=speechAudio;
      }
    }
    if (data.mode==='a2v') {
      if (!recordingAudio) throw new Error(StudioI18n.t("video.generation_add_recording_audio_to_video"));
      data.source_audio=recordingAudio;
      if (source) data.source_image=source;
    }
    if (data.mode==='first_last') {
      if (!end) throw new Error(StudioI18n.t("video.generation_add_ending_frame"));
      data.end_image=end;
    }
    if (data.mode==='characters') {
      if (!characters.length || characters.some(p=>!p.image)) throw new Error(StudioI18n.t("video.generation_add_image_each_character"));
      if (characters.some(p=>p.speech_text.trim() && !p.voice_audio)) throw new Error(StudioI18n.t("video.generation_add_voice_sample_each_line_dialogue"));
      data.characters=characters;
    }
    return data;
  }
  el('videoForm').onsubmit=async event=>{
    event.preventDefault();if(active)return;
    el('videoGenerate').disabled=true;StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.t("video.generation_input_and_gpu_validation_status")), true);
    try {
      // A scenario that was not made for the current idea, segment count and source frame is rewritten, never reused.
      if(segmentCount() && (plan.key!==planKey() || plan.prompts.some(p=>!p.trim())))await buildPlan();
      if(enhanceOn() && (enhanced.key!==enhanceKey() || !el('videoEnhanced').value.trim()))await buildEnhanced();
      const data=generationRequest();
      if(enhanceOn()){data.user_prompt=data.prompt;data.prompt=el('videoEnhanced').value.trim();if(isAnimate()&&el('videoEnhancedMotion').value.trim())data.motion_prompt=el('videoEnhancedMotion').value.trim();}
      active=await api('generate',data);el('videoSeed').value=active.seed;draft();el('videoCancel').disabled=false;el('generate').disabled=true;
      timer=setInterval(poll,1000);poll();
    } catch(error) {StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);el('videoGenerate').disabled=!available;StudioUI.notify({module:'Video',error:true,message:StudioI18n.remote(error.message),onLogs:()=>StudioUI.showLogs('Video','',logs)});}
  };
  el('videoCancel').onclick=async()=>{if(!active)return;el('videoCancel').disabled=true;try{await api('cancel',{id:active.id,cancel_token:active.cancel_token});}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);el('videoCancel').disabled=false;}};
  async function saveFolder(path) {
    const result=await api('settings',{save_dir:path});
    el('videoSaveDir').value=result.save_dir;
    StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.t("video.save_folder_video_saved")), true);
  }
  el('videoSaveSettings').onclick=async()=>{try{await saveFolder(el('videoSaveDir').value);}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('videoChooseSaveDir').onclick=()=>window.chooseStudioFolder(el('videoSaveDir').value,saveFolder,el('videoChooseSaveDir'));
  el('videoOpenSaveDir').onclick=async()=>{try{await StudioUI.openFolder(el('videoSaveDir').value);}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('videoCopySaveDir').onclick=()=>navigator.clipboard.writeText(el('videoSaveDir').value);
  el('videoPlanBuild').onclick=()=>{buildPlan().catch(()=>{});};
  el('videoEnhanceBuild').onclick=()=>{buildEnhanced().catch(()=>{});};
  el('videoEnhance').onchange=()=>{syncEnhance();draft();};
  el('videoSavePrompt').onclick=async()=>{try{await api('prompts',{action:'add',prompt:el('videoPrompt').value});await prompts();}catch(e){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('videoRefreshLogs').onclick=()=>logs(active?.id || last?.id || '');
  el('videoCopyLogs').onclick=()=>navigator.clipboard.writeText(el('videoLogs').textContent);
  window.addEventListener('beforeunload',event=>{if(active || exportJob || mixJob){event.preventDefault();event.returnValue='';}});
  async function fetchSourceImage(url) {
    const response=await fetch(url);
    if (!response.ok) throw new Error(StudioI18n.t("video.fetch_image_source_unavailable"));
    return response.blob();
  }
  window.addEventListener('visualforge:use-image-in-video',async event=>{
    try{await inputFrame(await fetchSourceImage(event.detail.url),'source');if(!el('videoPrompt').value)el('videoPrompt').value=event.detail.prompt || '';el('videoMode').value='i2v';modes();draft();module('video');}catch(error){StudioI18n.bind(el('videoStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);module('video');}
  });
  function imageActions() {
    for(const container of [el('images'),el('historyCards')])if(container)for(const link of container.querySelectorAll('a[href^="/api/result"]')){
      if(link.dataset.videoAction)continue;link.dataset.videoAction='1';const button=document.createElement('button');button.type='button';button.className='secondary';StudioI18n.bind(button, "textContent", () => (StudioI18n.t("video.button_use_video")), true);button.onclick=()=>window.dispatchEvent(new CustomEvent('visualforge:use-image-in-video',{detail:{url:link.href}}));link.parentElement.append(button);
    }
  }
  for(const id of ['images','historyCards'])if(el(id))new MutationObserver(imageActions).observe(el(id),{childList:true,subtree:true});
  try{const value=JSON.parse(localStorage.getItem('visualforge.video.draft.v1') || '{}');engineDrafts=value.engineDrafts || {};if(value.enhance===false)el('videoEnhance').checked=false;if(value.speech){el('videoSpeechText').value=value.speech.text || '';if(['clone','audio'].includes(value.speech.mode))el('videoSpeechMode').value=value.speech.mode;if(['Russian','English','Auto'].includes(value.speech.language))el('videoSpeechLanguage').value=value.speech.language;}selectedEngine=value.engine || 'freevideo-h3-int8';el('videoEngine').add(StudioI18n.option(() => ('Wan 2.2 TI2V-5B'), 'wan22-ti2v-5b'));el('videoEngine').add(StudioI18n.option(() => ('Wan 2.2 S2V-14B'), 'wan22-s2v-14b'));el('videoEngine').value=selectedEngine;el('videoPrompt').value=value.prompt || '';el('videoNegative').value=value.negative || '';el('videoSeed').value=value.seed || '';if(['t2v','i2v','first_last','characters','s2v','a2v','animate','replace'].includes(value.mode))el('videoMode').value=value.mode;if(['preview','standard','high'].includes(value.quality))el('videoQuality').value=value.quality;if(['39','243','1440'].includes(value.frames))el('videoDuration').value=value.frames;}catch{}
  if(!engineDrafts[selectedEngine])rememberEngine();
  modes();imageActions();
})();
