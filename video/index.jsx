import React from 'react';
import {AbsoluteFill, Composition, OffthreadVideo, Sequence, staticFile, registerRoot} from 'remotion';
const Clip=({start}) => <AbsoluteFill style={{backgroundColor:'#0c1410',justifyContent:'center'}}><OffthreadVideo src={staticFile('source.mp4')} startFrom={start} style={{width:'100%'}} /></AbsoluteFill>;
const Demo=()=> <AbsoluteFill style={{background:'#0c1410'}}><Sequence durationInFrames={420}><Clip start={105}/></Sequence><Sequence from={420} durationInFrames={420}><Clip start={1170}/></Sequence></AbsoluteFill>;
const Root=()=> <Composition id="TerraDemo" component={Demo} durationInFrames={840} fps={30} width={1280} height={720}/>;
registerRoot(Root);
