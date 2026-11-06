/** Private stdio protocol; never forward output to MCP or logs. */
import {enums} from '@evex-dev/linejs-types';
import {Client} from '@evex-dev/linejs';
import {BaseClient} from '@evex-dev/linejs/base';
import {MemoryStorage} from '@evex-dev/linejs/storage';
import {squareContext,executeSquare} from './square-actions.mjs';
import {messageOptions,messagePage} from './openchat.mjs';
import {noteUpdateBody} from './notes.mjs';
import {ownsPublicKey, sharedMessageCount} from './identity.mjs';
import {createInterface} from 'node:readline';
const output = process.stdout.write.bind(process.stdout);
console.log = console.warn = console.error = () => {};
let pendingOutput=Promise.resolve();
const emit = value => {pendingOutput=pendingOutput.then(()=>new Promise((resolve,reject)=>output(JSON.stringify(value)+'\n',error=>error?reject(error):resolve())));};
const mid = x => typeof x === 'string' && /^[uc][0-9a-f]{32}$/.test(x);
const ownerId=x=>typeof x==='string'&&/^(?:u[0-9a-f]{32}|U[A-Za-z0-9_-]{43})$/.test(x);
const home=x=>typeof x==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test(x);
const id = x => typeof x === 'string' && /^[0-9]{1,30}$/.test(x);
let stage = 'input';
let lastStatus=null;
let client, storage, req;
let identityVerified=false;
let activeDevice;
const savedState=()=>({token:client.base.authToken,storage:storage.getAll(),owner:req.owner,canonicalOwner:client.base.profile.mid,device:activeDevice});
const MAX = 32 * 1024 * 1024;
const fetchBounded = async (url, init = {}) => {
  const u = new URL(url instanceof Request ? url.url : url);
  if (u.protocol !== 'https:' || u.username || u.password || u.port || !['legy.line-apps.com','legy-jp.line-apps.com','gf.line.naver.jp','obs.line-apps.com'].includes(u.hostname)) throw Error('destination_denied');
  if(u.pathname==='/acct/lgn/sq/v1') stage='qr_session_or_code';
  else if(u.pathname==='/acct/lp/lgn/sq/v1') stage='qr_phone_wait';
  const phase=u.pathname.startsWith('/acct/lp/')?'phone_wait':u.pathname.startsWith('/acct/')?'qr_setup':u.pathname==='/CH4'?'channel_authorize':u.pathname.startsWith('/ext/album/')?'album_list':u.pathname.startsWith('/oa/')?'album_download':u.pathname==='/enc'?'encrypted_talk':u.pathname.startsWith('/mh/')?'notes':'talk';
  emit({event:'progress',phase});
  const res = await fetch(url, {...init, redirect:'error', signal:AbortSignal.any([AbortSignal.timeout(40000), ...(init.signal ? [init.signal] : []), ...(url instanceof Request ? [url.signal] : [])])});
  lastStatus=res.status;
  if (!res.body) return res;
  const reader = res.body.getReader(); let size=0;
  const stream = new ReadableStream({async pull(ctrl) {
    try {const r=await reader.read(); if(r.done){ctrl.close();return;} size+=r.value.length; if(size>MAX) {await reader.cancel();ctrl.error(Error('response_too_large'));return;} ctrl.enqueue(r.value);}
    catch {ctrl.error(Error('transport_failed'));}
  }, cancel(){return reader.cancel();}});
  return new Response(stream,{status:res.status,headers:res.headers});
};
try {
  const lines=createInterface({input:process.stdin,crlfDelay:Infinity});
  const {value:line}=await lines[Symbol.asyncIterator]().next(); lines.close();
  if(!line || line.length>8*1024*1024) throw Error('input_invalid');
  req=JSON.parse(line);
  if(!['square_preflight','square_execute','profile_image','pin_message','set_chat_bgm','note_like','note_comment','note_share','login','check','albums','photos','photo','notes','resolve_chat','note_get','note_create','note_update','note_delete','squares','square_chats','square_get','square_chat_get','square_members','square_messages'].includes(req.command) || !ownerId(req.owner)) throw Error('input_invalid');
  activeDevice=req.command==='login'?(req.device??'DESKTOPMAC'):(req.state?.device??'DESKTOPMAC');
  if(!['DESKTOPMAC','IOSIPAD'].includes(activeDevice)) throw Error('input_invalid');
  const freshDevice=req.command==='login'&&(req.state?.device??'DESKTOPMAC')!==activeDevice;
  storage=new MemoryStorage(freshDevice?{}:(req.state?.storage || {}));
  const init={device:activeDevice,endpoint:'legy-jp.line-apps.com',storage,fetch:fetchBounded};
  stage='authentication';
  const base=new BaseClient(init);
  base.on('update:authtoken',token=>{base.authToken=token;});
  if(req.command==='login') {
    base.on('qrcall',url=>emit({event:'qr',value:url}));
    base.on('pincall',pin=>emit({event:'pin',value:String(pin)}));
    await base.loginProcess.withQrCode({});
  } else {
    if(typeof req.state?.token!=='string') throw Error('additional_connection_required');
    base.authToken=req.state.token;
  }
  await base.loginProcess.ready();
  client=new Client(base);
  stage='identity';
  if(req.command==='login') {
    if(!Array.isArray(req.public_keys)||req.public_keys.length<1||req.public_keys.length>100) throw Error('owner_proof_unavailable');
    const keys=await client.base.talk.getE2EEPublicKeys();
    if(!Array.isArray(keys)) throw Error('owner_proof_unavailable');
    const same=ownsPublicKey(keys,req.public_keys);
    if(!same) throw Error('owner_mismatch');
  } else if(req.state.owner!==req.owner||req.state.canonicalOwner!==client.base.profile.mid) throw Error('owner_mismatch');
  identityVerified=true;
  let result;
  stage='content';
  const cursor=req.cursor??'';
  if(typeof cursor!=='string'||cursor.length>2048) throw Error('input_invalid');
  if(['photos','photo','notes','note_get','note_create','note_update','note_delete'].includes(req.command)&&!home(req.home)) throw Error('input_invalid');
  if(['photos','photo'].includes(req.command)&&!id(req.album_id)) throw Error('input_invalid');
  if(req.command==='resolve_chat') {
    stage='group_mapping';
    if(typeof req.name!=='string'||req.name.length>1000||!Array.isArray(req.message_ids)||req.message_ids.length<2||req.message_ids.length>20||!req.message_ids.every(id)) throw Error('group_mapping_unavailable');
    const joined=await client.base.talk.getAllChatMids({request:{withMemberChats:true},syncReason:'INTERNAL'});
    const mids=joined?.memberChatMids;
    if(!Array.isArray(mids)||mids.length>2000||!mids.every(mid)) throw Error('group_mapping_unavailable');
    const candidates=[];
    for(let i=0;i<mids.length;i+=100) {
      const page=await client.base.talk.getChats({chatMids:mids.slice(i,i+100),withMembers:false,withInvitees:false});
      if(!Array.isArray(page?.chats)) throw Error('group_mapping_unavailable');
      for(const chat of page.chats) if(chat.chatName===req.name&&mids.includes(chat.chatMid)) candidates.push(chat.chatMid);
    }
    if(candidates.length>5) throw Error('group_mapping_ambiguous');
    const matches=[];
    for(const candidate of new Set(candidates)) {
      const messages=await client.base.talk.getRecentMessagesV2({messageBoxId:candidate,messagesCount:100});
      if(sharedMessageCount(Array.isArray(messages)?messages.filter(m=>m?.to===candidate):messages,req.message_ids)>=2) matches.push(candidate);
    }
    if(matches.length!==1) throw Error('group_mapping_unavailable');
    result={home:matches[0],matched_messages:2};
  } else if(req.command==='albums') result=await client.base.moa.getAlbums({cursor});
  else if(req.command==='photos') result=await client.base.moa.getPhotos({chatId:req.home,albumId:req.album_id,cursor,pageSize:100});
  else if(req.command==='photo') {
    if(typeof req.oid!=='string'||!/^[A-Za-z0-9_-]{1,200}$/.test(req.oid)||!['a','o','v'].includes(req.sid)) throw Error('input_invalid');
    // Live Moa IMAGE_ORIGINAL resources use sid=o, omitted by upstream 3.6.4's helper.
    const channel=await client.base.moa.getAlbumChannelToken();
    const response=await client.base.fetch('https://'+client.base.endpoint+'/oa/r/album/'+req.sid+'/'+encodeURIComponent(req.oid), {
      method:'POST',headers:{...client.base.request.getHeader('GET'),'X-Line-ChannelToken':channel,'X-Line-Album':req.album_id,'X-Line-Mid':req.home},body:new Uint8Array(),signal:AbortSignal.timeout(40000)
    });
    if(!response.ok) throw Error('upstream_http_error');
    const bytes=new Uint8Array(await response.arrayBuffer());
    if(bytes.length>MAX) throw Error('response_too_large');
    result={bytes:Buffer.from(bytes).toString('base64')};
  } else if(['note_get','note_create','note_update','note_delete'].includes(req.command)) {
    if(req.command!=='note_create'&&!id(req.post_id)) throw Error('input_invalid');
    if(['note_create','note_update'].includes(req.command)&&(typeof req.text!=='string'||req.text.length<1||req.text.length>10000)) throw Error('input_invalid');
    await client.base.timeline.initTimeline();
    if(req.command==='note_get') {
      const params=new URLSearchParams({homeId:req.home,postId:req.post_id});
      const response=await client.base.fetch('https://'+client.base.endpoint+'/ext/note/nt/api/v57/post/get.json?'+params,{method:'POST',headers:{...client.base.timeline.timelineHeaders,'x-lhm':'GET'},body:new Uint8Array(),signal:AbortSignal.timeout(40000)});
      if(!response.ok) throw Error('upstream_http_error');result=await response.json();
    } else {
      const originalFetch=client.base.fetch.bind(client.base);
      client.base.fetch=(url,init)=>{
        const u=new URL(url instanceof Request?url.url:url);
        if(u.hostname!==client.base.endpoint||!['/mh/api/v57/post/create.json','/mh/api/v57/post/update.json','/mh/api/v57/post/delete.json'].includes(u.pathname)) throw Error('destination_denied');
        u.pathname=u.pathname.replace('/mh/','/ext/note/nt/');
        return originalFetch(u.toString(),init);
      };
      if(req.command==='note_create') result=await client.base.timeline.createPost({homeId:req.home,text:req.text,readPermissionType:'GROUP',readPermissionGids:[req.home],sourceType:'TALKROOM'});
      if(req.command==='note_update') {
        if(!req.contents||Array.isArray(req.contents)||typeof req.contents!=='object') throw Error('input_invalid');
        // Preserve media, locations, stickers and style verbatim. Text offsets no
        // longer describe the replacement text, so only text metadata is reset.
        const body=noteUpdateBody(req.home,req.post_id,req.contents,req.text);
        const response=await client.base.fetch('https://'+client.base.endpoint+'/mh/api/v57/post/update.json?'+new URLSearchParams({homeId:req.home}),{method:'POST',headers:{...client.base.timeline.timelineHeaders,'x-lhm':'POST'},body:JSON.stringify(body)});
        if(!response.ok) throw Error('upstream_http_error');result=await response.json();
      }
      if(req.command==='note_delete') result=await client.base.timeline.deletePost({homeId:req.home,postId:req.post_id});
    }
  } else if(req.command==='notes') {
    if(req.post_id!==undefined&&!id(req.post_id)) throw Error('input_invalid');
    if(req.updated_time!==undefined&&(!Number.isSafeInteger(req.updated_time)||req.updated_time<0)) throw Error('input_invalid');
    await client.base.timeline.initTimeline();
    const params=new URLSearchParams({homeId:req.home,sourceType:'TALKROOM',likeLimit:'0',commentLimit:'0'});
    if(req.post_id!==undefined) params.set('postId',req.post_id);
    if(req.updated_time!==undefined) params.set('updatedTime',String(req.updated_time));
    const response=await client.base.fetch('https://'+client.base.endpoint+'/ext/note/nt/api/v57/post/list.json?'+params,{
      method:'POST',headers:{...client.base.timeline.timelineHeaders,'x-lhm':'GET'},body:new Uint8Array(),signal:AbortSignal.timeout(40000)
    });
    if(!response.ok) throw Error('upstream_http_error');
    result=await response.json();
  } else if(req.command==='squares'||req.command==='square_chats') {
    if(typeof cursor!=='string'||cursor.length>2048) throw Error('input_invalid');
    if(req.command==='squares') result=await client.base.square.getJoinedSquares({limit:100,...(cursor?{continuationToken:cursor}:{})});
    else {
      const page=await client.base.square.fetchMyEvents({limit:100,...(cursor?{continuationToken:cursor}:{})});
      if(!Array.isArray(page.events)) throw Error('unexpected_schema');
      result={squareChats:page.events.map(e=>e.payload?.notifiedCreateSquareChatMember?.chat).filter(Boolean),continuationToken:page.continuationToken,source:'membership_events'};
    }
  } else if(['square_get','square_chat_get','square_members','square_messages'].includes(req.command)) {
    const prefix=req.command==='square_get'?'s':'m';
    if(typeof req.target!=='string'||!new RegExp('^'+prefix+'[0-9a-f]{32}$').test(req.target)) throw Error('input_invalid');
    if(req.command==='square_get') result=await client.base.square.getSquare({squareMid:req.target});
    if(req.command==='square_chat_get') result=await client.base.square.getSquareChat({squareChatMid:req.target});
    if(req.command==='square_members') result=await client.base.square.getSquareChatMembers({squareChatMid:req.target,limit:100,...(cursor?{continuationToken:cursor}:{})});
    if(req.command==='square_messages') {
      const page=await client.base.square.fetchSquareChatEvents(messageOptions(req.target,req.sync_token,req.continuation_token));
      result=messagePage(page,req.target);
    }
  }
  else if(req.command==='square_preflight') result=await squareContext(client,req.kind,req.payload);
  else if(req.command==='square_execute') result=await executeSquare(client,req.kind,req.payload,req.expected);
  else if(req.command==='profile_image') {
    if(typeof req.data!=='string'||req.data.length>7*1024*1024||!['image/png','image/jpeg'].includes(req.content_type)) throw Error('input_invalid');
    const bytes=Buffer.from(req.data,'base64');
    if(bytes.length<8||bytes.length>5*1024*1024||bytes.toString('base64')!==req.data) throw Error('input_invalid');
    const png=bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]));
    const jpeg=bytes[0]===255&&bytes[1]===216&&bytes[2]===255;
    if(!(req.content_type==='image/png'?png:jpeg)) throw Error('input_invalid');
    const uploaded=await client.uploadMyProfileImage(new Blob([bytes],{type:req.content_type}));
    if(typeof uploaded.objHash!=='string'||!uploaded.objHash) throw Error('upstream_failed');
    result={uploaded:true,applied:true};
  }
  else if(['pin_message','set_chat_bgm','note_like','note_comment','note_share'].includes(req.command)) {
    if(typeof req.home!=='string'||!/^c[0-9a-f]{32}$/.test(req.home)) throw Error('input_invalid');
    if(req.command==='pin_message') {
      if(!id(req.message_id)||typeof req.text!=='string'||!req.text.length||req.text.length>1000) throw Error('input_invalid');
      await client.base.talk.createChatRoomAnnouncement({reqSeq:await client.base.getReqseq(),chatRoomMid:req.home,type:'MESSAGE',contents:{text:req.text,link:`line://nv/chatMsg?chatId=${req.home}&messageId=${req.message_id}`}});
      result={applied:true};
    } else if(req.command==='set_chat_bgm') {
      if(typeof req.bgm_info!=='string'||req.bgm_info.length>2000) throw Error('input_invalid');
      await client.base.talk.updateChatRoomBGM({reqSeq:await client.base.getReqseq(),chatRoomMid:req.home,chatRoomBGMInfo:req.bgm_info});
      result={applied:true};
    } else {
      if(!id(req.post_id)) throw Error('input_invalid');
      if(req.command==='note_like') {
        if(!['1001','1002','1003','1004','1005','1006'].includes(req.reaction)) throw Error('input_invalid');
        result=await client.base.timeline.likePost({homeId:req.home,contentId:req.post_id,likeType:req.reaction,sourceType:'TALKROOM'});
      }
      if(req.command==='note_comment') {
        if(typeof req.text!=='string'||!req.text.length||req.text.length>10000) throw Error('input_invalid');
        result=await client.base.timeline.createComment({homeId:req.home,contentId:req.post_id,commentText:req.text,sourceType:'TALKROOM'});
      }
      if(req.command==='note_share') {
        result=await client.base.timeline.sharePost({postId:req.post_id,chatMid:req.home,homeId:req.home});
      }
    }
  }
  else result={connected:true};
  emit({event:'result',result,state:savedState()});
} catch(e) {
  const known=['group_mapping_unavailable','group_mapping_ambiguous','owner_proof_unavailable','owner_mismatch','additional_connection_required','square_not_member','square_role_required','square_message_not_found','square_own_message_required','square_context_changed','input_invalid','response_too_large','destination_denied','unexpected_schema'];
  const network = ['ECONNRESET','ECONNREFUSED','ETIMEDOUT','ENOTFOUND','UND_ERR_CONNECT_TIMEOUT'].includes(e?.cause?.code);
  const timeout = e?.name==='TimeoutError' || e?.name==='AbortError' || ['ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT'].includes(e?.cause?.code);
  const errorKind=['RequestError','TalkException','NotAuthorized','TypeError','ReferenceError','TimeoutError'].includes(e?.name)?e.name:'Other';
  const code=Number.isInteger(e?.data?.code)?e.data.code:Number.isInteger(e?.code)?e.code:stage==='authentication'&&Number.isInteger(enums.ErrorCode?.[e?.data?.code])?enums.ErrorCode[e.data.code]:null;
  emit({event:'error',diagnostic:{error_kind:errorKind,line_code:code,http_status:lastStatus},...(identityVerified?{state:savedState()}:{}),stage,reason:known.includes(e?.message)?e.message:timeout?'transport_timeout':network?'transport_failed':lastStatus>=400?'upstream_http_error':'upstream_failed'});
}
await pendingOutput;
process.exit(0);
