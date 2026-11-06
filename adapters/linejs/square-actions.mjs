/** Typed Square operations; no ordinary LINE MID is a Square identity. */
import {messageOptions,messagePage} from './openchat.mjs';
const targetId=x=>typeof x==='string'&&/^m[0-9a-f]{32}$/.test(x);
const memberId=x=>typeof x==='string'&&/^p[0-9a-f]{32}$/.test(x);
const squareId=x=>typeof x==='string'&&/^s[0-9a-f]{32}$/.test(x);
const msgId=x=>typeof x==='string'&&/^\d{1,30}$/.test(x);
export const squareKinds=['square_send_text','square_react','square_mark_read','square_unsend','square_rename_chat','square_leave_chat','square_send_media','square_invite','square_destroy_message','square_join','square_leave','square_create','square_rename'];
export async function squareContext(client,kind,payload) {
 if(['square_join','square_leave','square_create','square_rename'].includes(kind)) return communityContext(client,kind,payload);
 if(![...squareKinds,'square_media_read'].includes(kind)||!targetId(payload.target)) throw Error('input_invalid');
 const detail=await client.base.square.getSquareChat({squareChatMid:payload.target});
 const chat=detail?.squareChat, member=detail?.squareChatMember;
 if(chat?.squareChatMid!==payload.target||!squareId(chat.squareMid)||member?.squareChatMid!==payload.target||!memberId(member.squareMemberMid)||!['JOINED',1].includes(member.membershipState)||!['ALIVE',0].includes(chat.state)) throw Error('square_not_member');
 const community=await client.base.square.getSquare({squareMid:chat.squareMid});
 const own=community?.myMembership;
 if(community?.square?.mid!==chat.squareMid||own?.squareMid!==chat.squareMid||own?.squareMemberMid!==member.squareMemberMid||!['JOINED',2].includes(own.membershipState)) throw Error('square_not_member');
 if(!/^\d{1,19}$/.test(String(member.revision))||BigInt(member.revision)>9223372036854775807n) throw Error('unexpected_schema');
 const context={target:payload.target,square:chat.squareMid,member:member.squareMemberMid,role:own.role,revision:String(member.revision),chat_revision:String(chat.squareChatRevision),name:chat.name};
 if(['square_rename_chat','square_destroy_message'].includes(kind)&&!['ADMIN','CO_ADMIN',1,2].includes(own.role)) throw Error('square_role_required');
 const id=payload.message_id??payload.reply_to;
 if(id!==undefined&&id!==null) {
  if(!msgId(id)) throw Error('input_invalid');
  const page=messagePage(await client.base.square.fetchSquareChatEvents(messageOptions(payload.target)),payload.target);
  const rows=page.messages.filter(m=>String(m.id)===id);
  if(rows.length!==1||!memberId(rows[0].from)) throw Error('square_message_not_found');
  const m=rows[0];
  if(kind==='square_unsend'&&m.from!==member.squareMemberMid) throw Error('square_own_message_required');
  context.message={id:String(m.id),from:m.from,to:m.to,text:m.text??null,contentType:m.contentType??null};
 }
 return context;
}
export async function executeSquare(client,kind,payload,expected) {
 const context=await squareContext(client,kind,payload);
 if(JSON.stringify(context)!==JSON.stringify(expected)) throw Error('square_context_changed');
 let result;
 if(['square_join','square_leave','square_create','square_rename'].includes(kind)) return executeCommunity(client,kind,payload,context);
 if(kind==='square_send_media') {
  if(!['image','video','audio'].includes(payload.media_type)||typeof payload.data!=='string'||payload.data.length>36*1024*1024) throw Error('input_invalid');
  const bytes=Buffer.from(payload.data,'base64');
  if(!bytes.length||bytes.length>25*1024*1024||bytes.toString('base64')!==payload.data) throw Error('input_invalid');
  if(payload.duration_ms!=null&&(!Number.isInteger(payload.duration_ms)||payload.duration_ms<0||payload.duration_ms>86400000)) throw Error('input_invalid');
  const chat=await client.getSquareChat(payload.target);
  const method={image:'sendImage',video:'sendVideo',audio:'sendAudio'}[payload.media_type];
  const sent=await chat[method](new Blob([bytes]),'attachment',payload.duration_ms??undefined);
  if(!msgId(sent?.objId)||typeof sent.objHash!=='string'||!sent.objHash) throw Error('unexpected_schema');
  return {id:sent.objId,uploaded:true};
 }
 if(kind==='square_invite') {
  if(!Array.isArray(payload.invitees)||payload.invitees.length<1||payload.invitees.length>20||new Set(payload.invitees).size!==payload.invitees.length||!payload.invitees.every(x=>/^u[0-9a-f]{32}$/.test(x))) throw Error('input_invalid');
  const sent=await client.base.square.inviteIntoSquareChat({request:{squareChatMid:payload.target,inviteeMids:payload.invitees}});
  if(!Array.isArray(sent?.inviteeMids)||new Set(sent.inviteeMids).size!==sent.inviteeMids.length||sent.inviteeMids.some(x=>!payload.invitees.includes(x))) throw Error('unexpected_schema');
  return {invited_count:sent.inviteeMids.length,complete:sent.inviteeMids.length===payload.invitees.length};
 }
 if(kind==='square_destroy_message') await client.base.square.destroyMessage({squareChatMid:payload.target,messageId:payload.message_id});
 if(kind==='square_send_text') {
  if(typeof payload.text!=='string'||payload.text.length<1||payload.text.length>10000) throw Error('input_invalid');
  result=await client.base.square.sendMessage({squareChatMid:payload.target,text:payload.text,...(payload.reply_to?{relatedMessageId:payload.reply_to}:{})});
  const message=result?.createdSquareMessage?.message;
  if(!message||message.to!==payload.target||!msgId(String(message.id))) throw Error('unexpected_schema');
  return {id:String(message.id)};
 }
 if(kind==='square_react') {
  if(!['UNDO','NICE','LOVE','FUN','AMAZING','SAD','OMG'].includes(payload.reaction)) throw Error('input_invalid');
  await client.base.square.reactToMessage({request:{reqSeq:await client.base.getReqseq('sq'),squareChatMid:payload.target,messageId:payload.message_id,reactionType:payload.reaction}});
 }
 if(kind==='square_mark_read') await client.base.square.markAsRead({request:{squareChatMid:payload.target,messageId:payload.message_id,threadMid:''}});
 if(kind==='square_unsend') await client.base.square.unsendMessage({squareChatMid:payload.target,messageId:payload.message_id});
 if(kind==='square_rename_chat') {
  if(typeof payload.name!=='string'||!payload.name.length||payload.name.length>50) throw Error('input_invalid');
  await (await client.getSquareChat(payload.target)).updateName(payload.name);
 }
 if(kind==='square_leave_chat') await client.base.square.leaveSquareChat({request:{squareChatMid:payload.target,sayGoodbye:false,squareChatMemberRevision:BigInt(context.revision)}});
 return {applied:true};
}

const admin=role=>['ADMIN','CO_ADMIN',1,2].includes(role);
const text=(v,max)=>typeof v==='string'&&v.length>0&&v.length<=max;
async function communityContext(client,kind,p) {
 if(kind==='square_create') return {owner:client.base.profile.mid};
 if(!squareId(p.target)) throw Error('input_invalid');
 const value=await client.base.square.getSquare({squareMid:p.target});
 const square=value?.square, own=value?.myMembership;
 if(square?.mid!==p.target||!['ALIVE',0].includes(square.state)) throw Error('square_not_member');
 const bound=own?.squareMid===p.target&&memberId(own?.squareMemberMid);
 if(kind==='square_join') {
  if(own?.membershipState!=null&&(!bound||!['LEFT','JOIN_REQUEST_WITHDREW',4,8].includes(own.membershipState))) throw Error('square_join_state_conflict');
 } else if(!bound||!['JOINED',2].includes(own.membershipState)) throw Error('square_not_member');
 if(kind==='square_rename'&&!admin(own.role)) throw Error('square_role_required');
 return {owner:client.base.profile.mid,target:p.target,name:square.name,revision:String(square.revision),
  join_method:square.joinMethod?.type??null,member:bound?own.squareMemberMid:null,state:own?.membershipState??null,role:own?.role??null};
}
async function executeCommunity(client,kind,p,context) {
 if(['square_join','square_create'].includes(kind)&&!text(p.display_name,50)) throw Error('input_invalid');
 if(['square_create','square_rename'].includes(kind)&&!text(p.name,50)) throw Error('input_invalid');
 if(kind==='square_create') {
  if(typeof p.searchable!=='boolean'||typeof p.description!=='string'||p.description.length>500) throw Error('input_invalid');
  const r=await client.base.square.createSquare({squareName:p.name,displayName:p.display_name,description:p.description,searchable:p.searchable,squareJoinMethodType:'NONE'});
  if(!squareId(r?.square?.mid)||!memberId(r?.creator?.squareMemberMid)||r.creator.squareMid!==r.square.mid||!admin(r.creator.role)) throw Error('unexpected_schema');
  return {target:r.square.mid,created:true};
 }
 if(kind==='square_join') {
  if(typeof p.message!=='string'||p.message.length>500||(p.passcode!=null&&(typeof p.passcode!=='string'||p.passcode.length>32))) throw Error('input_invalid');
  const r=await client.base.square.joinSquare({squareMid:p.target,displayName:p.display_name,ableToReceiveMessage:true,passCode:p.passcode??undefined,joinMessage:p.message});
  if(r?.square?.mid!==p.target||r?.squareMember?.squareMid!==p.target||!memberId(r.squareMember.squareMemberMid)) throw Error('unexpected_schema');
  const state=r.squareMember.membershipState;
  if(!['JOINED','JOIN_REQUESTED','JOIN_RESERVED',1,2,9].includes(state)) throw Error('unexpected_schema');
  return {target:p.target,membership_state:state,joined:['JOINED',2].includes(state)};
 }
 if(kind==='square_leave') await client.base.square.leaveSquare({request:{squareMid:p.target}});
 if(kind==='square_rename') await (await client.getSquare(p.target)).updateName(p.name);
 return {target:p.target,applied:true};
}
export async function readSquareMedia(client,p) {
 const context=await squareContext(client,'square_media_read',{target:p.target,message_id:p.message_id});
 if(!['IMAGE','VIDEO','AUDIO','FILE',1,2,3,14].includes(context.message.contentType)||typeof p.preview!=='boolean') throw Error('input_invalid');
 // Use the established OBS route, never a message-supplied arbitrary URL.
 const blob=await client.base.obs.downloadMessageData({messageId:p.message_id,isPreview:p.preview,isSquare:true});
 if(!blob.size||blob.size>25*1024*1024) throw Error('response_too_large');
 return {data:Buffer.from(await blob.arrayBuffer()).toString('base64'),content_type:context.message.contentType,preview:p.preview};
}
