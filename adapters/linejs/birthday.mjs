/** Own ExtendedProfile only. Birthday Board/cards are a retired distinct service. */
export function birthdayView(profile) {
 if(!profile||typeof profile!=='object'||Array.isArray(profile)) throw Error('unexpected_schema');
 const b=profile.birthday;
 if(b==null) return {field_present:false,unset_semantics_unverified:true,dates_returned:false};
 if(typeof b!=='object'||Array.isArray(b)) throw Error('unexpected_schema');
 const out={field_present:true,dates_returned:false};
 for(const name of ['day','year']) {
  const value=b[name], enabled=b[name+'Enabled'], privacy=b[name+'PrivacyLevelType'];
  if(value!=null&&(typeof value!=='string'||value.length>32)) throw Error('unexpected_schema');
  if(enabled!=null&&typeof enabled!=='boolean') throw Error('unexpected_schema');
  if(privacy!=null&&![0,1,'PUBLIC','PRIVATE'].includes(privacy)) throw Error('unexpected_schema');
  out[name+'_value_present']=typeof value==='string'&&value.length>0;
  out[name+'_enabled']=enabled??null;
  out[name+'_privacy']=privacy==null?'UNKNOWN':privacy===0||privacy==='PUBLIC'?'PUBLIC':'PRIVATE';
 }
 out.unset_semantics_unverified=!out.day_value_present;
 return out;
}
export async function ownBirthday(client) {
 return {source:'own_extended_profile',birthday:birthdayView(await client.base.talk.getExtendedProfile({syncReason:'INTERNAL'})),friend_birthdays_supported:false};
}
