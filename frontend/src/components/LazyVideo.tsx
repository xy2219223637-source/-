import {useEffect,useRef,useState} from 'react';
import type {VideoHTMLAttributes} from 'react';

type LazyVideoProps = Omit<VideoHTMLAttributes<HTMLVideoElement>, 'src'> & {src:string};

export default function LazyVideo({src,preload='metadata',onPointerEnter,onFocus,...props}:LazyVideoProps){
  const videoRef=useRef<HTMLVideoElement>(null);
  const [nearViewport,setNearViewport]=useState(false);
  const [warm,setWarm]=useState(false);

  useEffect(()=>{
    const element=videoRef.current;
    if(!element)return;
    if(!('IntersectionObserver' in window)){setNearViewport(true);return}
    const observer=new IntersectionObserver(entries=>{
      if(entries.some(entry=>entry.isIntersecting)){setNearViewport(true);observer.disconnect()}
    },{rootMargin:'400px 0px'});
    observer.observe(element);
    return()=>observer.disconnect();
  },[]);

  useEffect(()=>{
    if(!warm||!nearViewport||!videoRef.current)return;
    videoRef.current.preload='auto';
    videoRef.current.load();
  },[warm,nearViewport,src]);

  return <video {...props} ref={videoRef} src={nearViewport?src:undefined} preload={nearViewport?preload:'none'}
    onPointerEnter={event=>{setNearViewport(true);setWarm(true);onPointerEnter?.(event)}}
    onFocus={event=>{setNearViewport(true);setWarm(true);onFocus?.(event)}}/>;
}
