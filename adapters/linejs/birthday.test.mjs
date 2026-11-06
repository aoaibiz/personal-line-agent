import test from 'node:test';
import assert from 'node:assert/strict';
import {birthdayView,ownBirthday} from './birthday.mjs';
test('birthday absent is not invented unset or private; real flags remain separate',()=>{
 assert.equal(birthdayView({}).unset_semantics_unverified,true);
 const value=birthdayView({birthday:{day:'01-01',year:'2000',dayEnabled:true,yearEnabled:false,dayPrivacyLevelType:'PUBLIC',yearPrivacyLevelType:1}});
 assert.equal(value.day_value_present,true);assert.equal(value.year_privacy,'PRIVATE');assert.equal(value.dates_returned,false);
 assert.equal(JSON.stringify(value).includes('2000'),false);
 assert.throws(()=>birthdayView({birthday:{dayPrivacyLevelType:77}}),/schema/);
});
test('birthday upstream failure cannot become empty profile success',async()=>{
 await assert.rejects(ownBirthday({base:{talk:{getExtendedProfile:async()=>{throw Error('upstream_failed');}}}}),/upstream/);
});
